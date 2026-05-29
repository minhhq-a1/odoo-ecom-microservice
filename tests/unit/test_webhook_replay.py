"""Replay-nonce fail-open behavior."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from src.api.routers.webhooks import _is_replay, _mark_processed


@pytest.mark.asyncio
async def test_first_seen_not_replay() -> None:
    fake = AsyncMock()
    fake.exists = AsyncMock(return_value=0)  # key not present
    with patch("src.api.routers.webhooks.get_redis", return_value=fake):
        assert await _is_replay("sig_abcdef") is False


@pytest.mark.asyncio
async def test_duplicate_is_replay() -> None:
    fake = AsyncMock()
    fake.exists = AsyncMock(return_value=1)  # key already present
    with patch("src.api.routers.webhooks.get_redis", return_value=fake):
        assert await _is_replay("sig_abcdef") is True


@pytest.mark.asyncio
async def test_redis_down_fails_open(caplog) -> None:
    fake = AsyncMock()
    fake.exists = AsyncMock(side_effect=RedisConnectionError("connection refused"))
    with patch("src.api.routers.webhooks.get_redis", return_value=fake):
        # Must NOT raise — fail-open → not_replay
        assert await _is_replay("sig_abcdef") is False


@pytest.mark.asyncio
async def test_redis_timeout_fails_open() -> None:
    from redis.exceptions import TimeoutError as RedisTimeoutError
    fake = AsyncMock()
    fake.exists = AsyncMock(side_effect=RedisTimeoutError("timeout"))
    with patch("src.api.routers.webhooks.get_redis", return_value=fake):
        assert await _is_replay("sig_abcdef") is False


@pytest.mark.asyncio
async def test_mark_processed_writes_nonce() -> None:
    """Round 9 P1: nonce recorded only after successful commit."""
    fake = AsyncMock()
    fake.set = AsyncMock(return_value=True)
    with patch("src.api.routers.webhooks.get_redis", return_value=fake):
        await _mark_processed("sig_abcdef")
    fake.set.assert_awaited_once()
    args, kwargs = fake.set.call_args
    assert args[0].startswith("webhook:nonce:shopee:")
    assert kwargs.get("ex") == 300


@pytest.mark.asyncio
async def test_mark_processed_redis_down_does_not_raise() -> None:
    fake = AsyncMock()
    fake.set = AsyncMock(side_effect=RedisConnectionError("down"))
    with patch("src.api.routers.webhooks.get_redis", return_value=fake):
        await _mark_processed("sig_abcdef")  # must swallow
