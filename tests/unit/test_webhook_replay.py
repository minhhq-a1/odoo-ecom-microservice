"""Replay-nonce behavior + Redis-down DB fallback (P1 review fix)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from src.api.routers.webhooks import _is_replay, _mark_processed


@pytest.mark.asyncio
async def test_first_seen_not_replay() -> None:
    fake = AsyncMock()
    fake.exists = AsyncMock(return_value=0)  # key not present
    db = AsyncMock()
    with patch("src.api.routers.webhooks.get_redis", return_value=fake):
        assert await _is_replay("sig_abcdef", db) is False
    db.scalar.assert_not_called()  # Redis up → no DB fallback


@pytest.mark.asyncio
async def test_duplicate_is_replay() -> None:
    fake = AsyncMock()
    fake.exists = AsyncMock(return_value=1)  # key already present
    db = AsyncMock()
    with patch("src.api.routers.webhooks.get_redis", return_value=fake):
        assert await _is_replay("sig_abcdef", db) is True


@pytest.mark.asyncio
async def test_redis_down_db_fallback_detects_replay() -> None:
    """Redis down: an already-ingested signature in webhook_event_log is
    still caught as a replay (no fail-open hole)."""
    fake = AsyncMock()
    fake.exists = AsyncMock(side_effect=RedisConnectionError("connection refused"))
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=123)  # prior signature_valid row exists
    with patch("src.api.routers.webhooks.get_redis", return_value=fake):
        assert await _is_replay("sig_abcdef", db) is True
    db.scalar.assert_awaited_once()


@pytest.mark.asyncio
async def test_redis_down_db_fallback_new_event_processes() -> None:
    """Redis down: a never-seen signature has no DB row → not a replay, so
    the event still processes (no silent loss)."""
    fake = AsyncMock()
    fake.exists = AsyncMock(side_effect=RedisConnectionError("down"))
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=None)  # no prior row
    with patch("src.api.routers.webhooks.get_redis", return_value=fake):
        assert await _is_replay("sig_abcdef", db) is False
    db.scalar.assert_awaited_once()


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
