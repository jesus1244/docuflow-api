import json
import time

import httpx
import pytest
import respx
from httpx import AsyncClient

from docuflow.webhooks import service
from docuflow.webhooks.service import SIGNATURE_HEADER, sign_payload, verify_signature
from tests.conftest import InlineQueue

HOOK_URL = "https://hooks.example.com/docuflow"


@pytest.fixture(autouse=True)
def public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """Resolve hostnames without the network: *.internal is private, everything else public."""

    async def fake_resolve(hostname: str) -> list[str]:
        return ["10.0.0.5"] if hostname.endswith(".internal") else ["93.184.216.34"]

    monkeypatch.setattr(service, "resolve_host", fake_resolve)


async def create_hook(client: AsyncClient, headers: dict[str, str], **extra: object) -> dict:  # type: ignore[type-arg]
    response = await client.post("/webhooks", json={"url": HOOK_URL, **extra}, headers=headers)
    assert response.status_code == 201, response.text
    body: dict = response.json()  # type: ignore[type-arg]
    return body


def test_signature_roundtrip() -> None:
    body = b'{"hello":"world"}'
    header = sign_payload("whsec_x", body, int(time.time()))
    assert verify_signature("whsec_x", body, header)
    assert not verify_signature("whsec_other", body, header)
    assert not verify_signature("whsec_x", body + b" ", header)
    assert not verify_signature("whsec_x", body, sign_payload("whsec_x", body, 1_000))  # too old


@pytest.mark.parametrize(
    "url",
    ["http://127.0.0.1/hook", "http://localhost/hook", "https://db.internal/hook", "ftp://x.com"],
)
async def test_rejects_private_or_invalid_urls(
    client: AsyncClient, auth_headers: dict[str, str], url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def resolve(hostname: str) -> list[str]:
        return ["127.0.0.1"] if hostname in {"localhost", "127.0.0.1"} else ["10.0.0.5"]

    monkeypatch.setattr(service, "resolve_host", resolve)
    response = await client.post("/webhooks", json={"url": url}, headers=auth_headers)
    assert response.status_code == 422


async def test_create_returns_secret_once(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    created = await create_hook(client, auth_headers)
    assert created["secret"].startswith("whsec_")
    assert set(created["events"]) == {"document.completed", "document.failed", "document.reviewed"}
    listed = (await client.get("/webhooks", headers=auth_headers)).json()
    assert "secret" not in listed[0]


@respx.mock
async def test_ping_is_delivered_with_valid_signature(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    route = respx.post(HOOK_URL).mock(return_value=httpx.Response(200))
    hook = await create_hook(client, auth_headers)

    response = await client.post(f"/webhooks/{hook['id']}/ping", headers=auth_headers)
    assert response.status_code == 202

    request = route.calls.last.request
    assert verify_signature(hook["secret"], request.content, request.headers[SIGNATURE_HEADER])
    assert json.loads(request.content)["event"] == "ping"

    deliveries = (
        await client.get(f"/webhooks/{hook['id']}/deliveries", headers=auth_headers)
    ).json()
    assert deliveries[0]["status"] == "succeeded"
    assert deliveries[0]["response_status"] == 200


@respx.mock
async def test_document_completed_triggers_webhook(
    client: AsyncClient, auth_headers: dict[str, str], invoice_pdf: bytes
) -> None:
    route = respx.post(HOOK_URL).mock(return_value=httpx.Response(204))
    await create_hook(client, auth_headers, events=["document.completed"])

    await client.post(
        "/documents",
        headers=auth_headers,
        files={"file": ("i.pdf", invoice_pdf, "application/pdf")},
    )
    payload = json.loads(route.calls.last.request.content)
    assert payload["event"] == "document.completed"
    assert payload["data"]["extracted_data"]["total"] == 138.05


@respx.mock
async def test_failed_delivery_is_scheduled_for_retry(
    client: AsyncClient, auth_headers: dict[str, str], inline_queue: InlineQueue
) -> None:
    respx.post(HOOK_URL).mock(return_value=httpx.Response(500))
    hook = await create_hook(client, auth_headers)
    await client.post(f"/webhooks/{hook['id']}/ping", headers=auth_headers)

    assert inline_queue.retries == ["deliver_webhook"]
    delivery = (
        await client.get(f"/webhooks/{hook['id']}/deliveries", headers=auth_headers)
    ).json()[0]
    assert delivery["status"] == "pending"
    assert delivery["attempts"] == 1
    assert delivery["last_error"] == "HTTP 500"


@respx.mock
async def test_delivery_gives_up_after_max_attempts(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    from docuflow.webhooks.tasks import deliver_webhook

    respx.post(HOOK_URL).mock(side_effect=httpx.ConnectError("boom"))
    hook = await create_hook(client, auth_headers)
    ping = (await client.post(f"/webhooks/{hook['id']}/ping", headers=auth_headers)).json()

    # attempt 1 ran inline; attempts 2 and 3 (max) run here
    with pytest.raises(Exception, match="Retry"):
        await deliver_webhook({}, ping["id"])
    assert await deliver_webhook({}, ping["id"]) == "failed"


async def test_disabled_endpoint_receives_nothing(
    client: AsyncClient, auth_headers: dict[str, str], inline_queue: InlineQueue, invoice_pdf: bytes
) -> None:
    hook = await create_hook(client, auth_headers)
    patched = await client.patch(
        f"/webhooks/{hook['id']}", json={"is_active": False}, headers=auth_headers
    )
    assert patched.json()["is_active"] is False

    await client.post(
        "/documents",
        headers=auth_headers,
        files={"file": ("i.pdf", invoice_pdf, "application/pdf")},
    )
    assert "deliver_webhook" not in inline_queue.names()
