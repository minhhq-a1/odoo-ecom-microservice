"""Unit tests for redis module."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_get_redis() -> None:
    """get_redis should return a Redis instance."""
    with patch("src.core.redis.aioredis.from_url") as mock_from_url:
        mock_redis = AsyncMock()
        mock_from_url.return_value = mock_redis

        from src.core.redis import get_redis

        redis = await get_redis()
        assert redis is not None


def test_redis_url_from_settings() -> None:
    """Redis should use URL from settings."""
    from src.core.config import settings

    assert settings.REDIS_URL is not None
    assert "redis://" in str(settings.REDIS_URL)
