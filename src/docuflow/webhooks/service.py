import asyncio
import hashlib
import hmac
import ipaddress
import json
import time
from collections.abc import Sequence
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from docuflow.core.config import get_settings
from docuflow.core.database import utcnow
from docuflow.core.exceptions import AppError, NotFoundError
from docuflow.core.queue import get_queue
from docuflow.core.security import generate_webhook_secret
from docuflow.webhooks.models import DeliveryStatus, WebhookDelivery, WebhookEndpoint, WebhookEvent
from docuflow.webhooks.schemas import WebhookCreate, WebhookUpdate

SIGNATURE_HEADER = "X-DocuFlow-Signature"
USER_AGENT = "DocuFlow-Webhooks/1.0"


class UnsafeWebhookUrlError(AppError):
    status_code = 422
    code = "unsafe_webhook_url"
    message = "Webhook URL must be a public http(s) address"


# ---------------------------------------------------------------- SSRF protection


async def assert_public_url(url: str) -> None:
    """Reject URLs that resolve to private, loopback or link-local addresses (SSRF guard)."""
    if get_settings().webhook_allow_private_hosts:
        return
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise UnsafeWebhookUrlError()
    try:
        addresses = await resolve_host(parts.hostname)
    except OSError as exc:
        raise UnsafeWebhookUrlError("Webhook host could not be resolved") from exc
    if not addresses or any(not ipaddress.ip_address(a).is_global for a in addresses):
        raise UnsafeWebhookUrlError()


async def resolve_host(hostname: str) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(hostname, None)
    return [str(info[4][0]) for info in infos]


# ---------------------------------------------------------------- signing


def sign_payload(secret: str, body: bytes, timestamp: int) -> str:
    """Stripe-style signature: t=<unix>,v1=<hex hmac-sha256 of "<t>.<body>">."""
    mac = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256)
    return f"t={timestamp},v1={mac.hexdigest()}"


def verify_signature(secret: str, body: bytes, header: str, tolerance_seconds: int = 300) -> bool:
    """Reference implementation for receivers (also used by the tests)."""
    try:
        parts = dict(item.split("=", 1) for item in header.split(","))
        timestamp = int(parts["t"])
    except (KeyError, ValueError):
        return False
    if abs(time.time() - timestamp) > tolerance_seconds:
        return False
    expected = sign_payload(secret, body, timestamp)
    return hmac.compare_digest(expected, header)


# ---------------------------------------------------------------- CRUD


async def create_endpoint(
    session: AsyncSession, owner_id: UUID, data: WebhookCreate
) -> WebhookEndpoint:
    url = str(data.url)
    await assert_public_url(url)
    endpoint = WebhookEndpoint(
        owner_id=owner_id,
        url=url,
        description=data.description,
        events=[e.value for e in data.events],
        secret=generate_webhook_secret(),
    )
    session.add(endpoint)
    await session.commit()
    return endpoint


async def list_endpoints(session: AsyncSession, owner_id: UUID) -> Sequence[WebhookEndpoint]:
    rows = await session.scalars(
        select(WebhookEndpoint)
        .where(WebhookEndpoint.owner_id == owner_id)
        .order_by(WebhookEndpoint.created_at.desc())
    )
    return rows.all()


async def get_owned_endpoint(
    session: AsyncSession, owner_id: UUID, endpoint_id: UUID
) -> WebhookEndpoint:
    endpoint = await session.get(WebhookEndpoint, endpoint_id)
    if endpoint is None or endpoint.owner_id != owner_id:
        raise NotFoundError("Webhook endpoint not found")
    return endpoint


async def update_endpoint(
    session: AsyncSession, endpoint: WebhookEndpoint, data: WebhookUpdate
) -> WebhookEndpoint:
    changes = data.model_dump(exclude_unset=True)
    if "events" in changes and changes["events"] is not None:
        changes["events"] = sorted({WebhookEvent(e).value for e in changes["events"]} - {"ping"})
    for field, value in changes.items():
        if value is not None:
            setattr(endpoint, field, value)
    await session.commit()
    return endpoint


async def list_deliveries(
    session: AsyncSession, endpoint_id: UUID, limit: int = 50
) -> Sequence[WebhookDelivery]:
    rows = await session.scalars(
        select(WebhookDelivery)
        .where(WebhookDelivery.endpoint_id == endpoint_id)
        .order_by(WebhookDelivery.created_at.desc())
        .limit(limit)
    )
    return rows.all()


# ---------------------------------------------------------------- dispatch & delivery


async def dispatch_event(
    session: AsyncSession, owner_id: UUID, event: WebhookEvent, data: dict[str, Any]
) -> list[WebhookDelivery]:
    """Create one delivery per subscribed endpoint and enqueue it for async sending."""
    query = select(WebhookEndpoint).where(
        WebhookEndpoint.owner_id == owner_id, WebhookEndpoint.is_active.is_(True)
    )
    if event != WebhookEvent.PING:
        query = query.where(WebhookEndpoint.events.contains([event.value]))
    endpoints = (await session.scalars(query)).all()
    return await _create_deliveries(session, endpoints, event, data)


async def send_ping(session: AsyncSession, endpoint: WebhookEndpoint) -> WebhookDelivery:
    deliveries = await _create_deliveries(
        session, [endpoint], WebhookEvent.PING, {"message": "Hello from DocuFlow"}
    )
    return deliveries[0]


async def _create_deliveries(
    session: AsyncSession,
    endpoints: Sequence[WebhookEndpoint],
    event: WebhookEvent,
    data: dict[str, Any],
) -> list[WebhookDelivery]:
    deliveries = []
    for endpoint in endpoints:
        delivery = WebhookDelivery(endpoint_id=endpoint.id, event=event.value, payload={})
        session.add(delivery)
        await session.flush()  # assigns id, used inside the payload for receiver idempotency
        delivery.payload = {
            "id": str(delivery.id),
            "event": event.value,
            "created_at": utcnow().isoformat(),
            "data": data,
        }
        deliveries.append(delivery)
    await session.commit()
    for delivery in deliveries:
        await get_queue().enqueue("deliver_webhook", str(delivery.id))
    return deliveries


async def attempt_delivery(
    session: AsyncSession, delivery: WebhookDelivery, http: httpx.AsyncClient
) -> bool:
    """Send one attempt. Returns True on 2xx. Always records the outcome."""
    endpoint = await session.get(WebhookEndpoint, delivery.endpoint_id)
    if endpoint is None or not endpoint.is_active:
        delivery.status = DeliveryStatus.FAILED
        delivery.last_error = "Endpoint deleted or disabled"
        await session.commit()
        return True  # nothing left to retry

    body = json.dumps(delivery.payload, separators=(",", ":"), default=str).encode()
    delivery.attempts += 1
    try:
        await assert_public_url(endpoint.url)  # re-check at send time (DNS rebinding)
        response = await http.post(
            endpoint.url,
            content=body,
            headers={
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
                SIGNATURE_HEADER: sign_payload(endpoint.secret, body, int(time.time())),
                "X-DocuFlow-Event": delivery.event,
                "X-DocuFlow-Delivery": str(delivery.id),
            },
        )
        delivery.response_status = response.status_code
        ok = response.is_success
        delivery.last_error = None if ok else f"HTTP {response.status_code}"
    except (httpx.HTTPError, AppError) as exc:
        ok = False
        delivery.last_error = f"{type(exc).__name__}: {exc}"[:1000]

    if ok:
        delivery.status = DeliveryStatus.SUCCEEDED
        delivery.delivered_at = utcnow()
    elif delivery.attempts >= get_settings().webhook_max_attempts:
        delivery.status = DeliveryStatus.FAILED
    await session.commit()
    return ok
