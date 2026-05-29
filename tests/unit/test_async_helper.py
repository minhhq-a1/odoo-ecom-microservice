"""Unit tests for async_helper."""

from __future__ import annotations

import asyncio

from src.workers._async_helper import run_async


def test_run_async_simple_coroutine() -> None:
    """run_async should execute async function and return result."""

    async def simple_coro() -> str:
        return "success"

    result = run_async(simple_coro())
    assert result == "success"


def test_run_async_with_await() -> None:
    """run_async should handle awaited coroutines."""

    async def async_add(a: int, b: int) -> int:
        await asyncio.sleep(0)
        return a + b

    result = run_async(async_add(2, 3))
    assert result == 5


def test_run_async_exception_propagates() -> None:
    """run_async should propagate exceptions from async functions."""
    import pytest

    async def failing_coro() -> None:
        raise ValueError("Test error")

    with pytest.raises(ValueError, match="Test error"):
        run_async(failing_coro())
