"""Real-time events: workers publish to a per-user Redis channel; WebSocket clients subscribe."""

import json
from typing import Any
from uuid import UUID

from docuflow.core.redis import get_redis


def user_channel(user_id: UUID | str) -> str:
    return f"events:user:{user_id}"


async def publish_event(user_id: UUID, event: str, data: dict[str, Any]) -> None:
    message = json.dumps({"event": event, "data": data}, default=str)
    await get_redis().publish(user_channel(user_id), message)
