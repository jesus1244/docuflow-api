from collections.abc import Awaitable, Callable

from fastapi import Request

from docuflow.core.exceptions import RateLimitedError
from docuflow.core.redis import get_redis


def rate_limit(
    scope: str, limit: int, window_seconds: int = 60
) -> Callable[[Request], Awaitable[None]]:
    """Fixed-window rate limiter backed by Redis, keyed by client IP and scope.

    Usage: dependencies=[Depends(rate_limit("login", 10))]
    """

    async def dependency(request: Request) -> None:
        client_ip = request.client.host if request.client else "unknown"
        key = f"ratelimit:{scope}:{client_ip}"
        redis = get_redis()
        async with redis.pipeline(transaction=True) as pipe:
            pipe.incr(key)
            pipe.expire(key, window_seconds, nx=True)
            count, _ = await pipe.execute()
        if int(count) > limit:
            ttl = await redis.ttl(key)
            raise RateLimitedError(details={"retry_after_seconds": max(ttl, 1)})

    return dependency
