"""WebSocket endpoint that streams the user's events (document progress, completions, failures)."""

import asyncio
import contextlib

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status

from docuflow.auth.dependencies import user_from_access_token
from docuflow.core.database import SessionFactory
from docuflow.core.events import user_channel
from docuflow.core.exceptions import UnauthorizedError
from docuflow.core.redis import get_redis

router = APIRouter(tags=["realtime"])


@router.websocket("/ws")
async def events_socket(websocket: WebSocket, token: str = Query()) -> None:
    # Browsers cannot set headers on WebSocket requests, so the access token comes as a query param.
    try:
        async with SessionFactory() as session:
            user = await user_from_access_token(session, token)
    except UnauthorizedError:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()
    pubsub = get_redis().pubsub()
    await pubsub.subscribe(user_channel(user.id))

    async def forward() -> None:
        async for message in pubsub.listen():
            if message["type"] == "message":
                await websocket.send_text(message["data"])

    async def receive() -> None:
        # Reading lets us detect disconnects; clients may also send "ping" keep-alives.
        while True:
            if await websocket.receive_text() == "ping":
                await websocket.send_text('{"event":"pong"}')

    tasks = [asyncio.create_task(forward()), asyncio.create_task(receive())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    except WebSocketDisconnect:
        pass
    finally:
        for task in tasks:
            task.cancel()
        with contextlib.suppress(Exception):
            await pubsub.unsubscribe()
            await pubsub.aclose()  # type: ignore[no-untyped-call]
