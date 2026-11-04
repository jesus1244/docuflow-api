from redis.asyncio import Redis

from docuflow.core.config import get_settings

_redis: Redis | None = None


def get_redis() -> Redis:
    global _redis  # noqa: PLW0603 - process-wide connection pool
    if _redis is None:
        _redis = Redis.from_url(get_settings().redis_url, decode_responses=True)
    return _redis


def set_redis(client: Redis | None) -> None:
    """Swap the client (used by tests)."""
    global _redis  # noqa: PLW0603
    _redis = client


async def close_redis() -> None:
    if _redis is not None:
        await _redis.aclose()
