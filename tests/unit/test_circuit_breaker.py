"""Circuit breaker behavior tests."""

from __future__ import annotations

import asyncio

import pytest

from src.core.circuit_breaker import CBState, CircuitBreaker
from src.core.exceptions import CircuitOpenError


@pytest.mark.asyncio
async def test_breaker_opens_after_threshold() -> None:
    cb = CircuitBreaker("test", failure_threshold=3, open_timeout_seconds=60)

    async def boom() -> None:
        raise RuntimeError("boom")

    for _ in range(3):
        with pytest.raises(RuntimeError):
            await cb.call(boom)
    assert cb.state is CBState.OPEN

    with pytest.raises(CircuitOpenError):
        await cb.call(boom)


@pytest.mark.asyncio
async def test_breaker_half_open_closes_on_success() -> None:
    cb = CircuitBreaker("test2", failure_threshold=2, open_timeout_seconds=0)

    async def boom() -> None:
        raise RuntimeError("x")

    async def ok() -> int:
        return 42

    for _ in range(2):
        with pytest.raises(RuntimeError):
            await cb.call(boom)
    assert cb.state is CBState.OPEN

    await asyncio.sleep(0.01)
    assert await cb.call(ok) == 42
    assert cb.state is CBState.CLOSED
