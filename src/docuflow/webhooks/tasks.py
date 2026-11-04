from typing import Any
from uuid import UUID

import httpx
from arq import Retry

from docuflow.core.config import get_settings
from docuflow.core.database import SessionFactory
from docuflow.webhooks.models import DeliveryStatus, WebhookDelivery
from docuflow.webhooks.service import attempt_delivery


def backoff_seconds(attempt: int) -> int:
    """Exponential backoff: 10s, 40s, 160s, 640s..."""
    return int(10 * 4 ** (attempt - 1))


async def deliver_webhook(ctx: dict[str, Any], delivery_id: str) -> str:
    http: httpx.AsyncClient | None = ctx.get("http")
    owns_client = http is None
    if http is None:
        http = httpx.AsyncClient(timeout=get_settings().webhook_timeout_seconds)
    try:
        async with SessionFactory() as session:
            delivery = await session.get(WebhookDelivery, UUID(delivery_id))
            if delivery is None or delivery.status != DeliveryStatus.PENDING:
                return "skipped"
            if await attempt_delivery(session, delivery, http):
                return delivery.status.value
            if delivery.status == DeliveryStatus.PENDING:
                raise Retry(defer=backoff_seconds(delivery.attempts))
            return delivery.status.value
    finally:
        if owns_client:
            await http.aclose()
