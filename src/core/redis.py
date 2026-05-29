"""Redis client singleton."""
from __future__ import annotations

from typing import TYPE_CHECKING

import redis.asyncio as aioredis

from src.core.config import settings

if TYPE_CHECKING:
    from redis.asyncio import Redis

_redis: Redis | None = None


async def get_redis() -> Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(
            str(settings.REDIS_URL),
            encoding="utf-8",
            decode_responses=True,
            max_connections=50,
            socket_connect_timeout=5,
            socket_keepalive=True,
        )
    return _redis


async def close_redis() -> None:
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None
