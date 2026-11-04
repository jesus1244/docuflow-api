import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from httpx import AsyncClient

from docuflow.core.events import publish_event, user_channel
from docuflow.core.exceptions import RateLimitedError
from docuflow.core.rate_limit import rate_limit
from docuflow.core.redis import get_redis
from docuflow.core.storage import LocalStorage


async def test_health_endpoints(client: AsyncClient) -> None:
    base = "http://test"
    assert (await client.get(f"{base}/health/live")).json() == {"status": "ok"}
    ready = await client.get(f"{base}/health/ready")
    assert ready.status_code == 200
    assert ready.json()["database"] == "ok"


async def test_request_id_is_echoed(client: AsyncClient) -> None:
    response = await client.get("/auth/me", headers={"X-Request-ID": "abc123"})
    assert response.headers["x-request-id"] == "abc123"


async def test_openapi_is_published(client: AsyncClient) -> None:
    spec = (await client.get("/openapi.json")).json()
    assert "/api/v1/documents" in spec["paths"]


async def test_rate_limiter_blocks_after_limit() -> None:
    limiter = rate_limit("test", limit=2)
    request = SimpleNamespace(client=SimpleNamespace(host="1.2.3.4"))
    await limiter(request)  # type: ignore[arg-type]
    await limiter(request)  # type: ignore[arg-type]
    with pytest.raises(RateLimitedError):
        await limiter(request)  # type: ignore[arg-type]


async def test_events_are_published_to_user_channel() -> None:
    user_id = uuid4()
    pubsub = get_redis().pubsub()
    await pubsub.subscribe(user_channel(user_id))
    await pubsub.get_message(timeout=1)  # subscription confirmation

    await publish_event(user_id, "document.completed", {"id": "x"})
    message = None
    for _ in range(20):
        message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.1)
        if message:
            break
        await asyncio.sleep(0.05)
    assert message is not None
    assert json.loads(message["data"]) == {"event": "document.completed", "data": {"id": "x"}}
    await pubsub.aclose()


async def test_local_storage_blocks_path_traversal(tmp_path: object) -> None:
    storage = LocalStorage(str(tmp_path))
    await storage.put("a/b.pdf", b"data", "application/pdf")
    assert await storage.get("a/b.pdf") == b"data"
    with pytest.raises(ValueError, match="Invalid storage key"):
        await storage.get("../../etc/passwd")


def test_worker_entrypoint_registers_every_model() -> None:
    """Regression: the worker once failed because auth models were not imported."""
    import subprocess
    import sys

    code = (
        "import docuflow.worker\n"
        "from sqlalchemy.orm import configure_mappers\n"
        "from docuflow.core.database import Base\n"
        "configure_mappers()\n"
        "assert {'auth_user', 'document', 'webhook_delivery'} <= set(Base.metadata.tables)\n"
    )
    result = subprocess.run(  # noqa: S603 - fixed command, no user input
        [sys.executable, "-c", code], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
