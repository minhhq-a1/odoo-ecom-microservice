"""Helper to run async code from sync Celery task.

Celery prefork pool runs one task at a time per worker process, so we
cache a persistent event loop per process. Module-level async resources
(SQLAlchemy async engine, Redis singleton) bind connections/pools to the
loop they were first used on; closing the loop between tasks invalidates
those pools and causes "Event loop is closed" / cross-loop errors on
subsequent tasks. Keep the loop alive for the worker's lifetime.
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    from collections.abc import Awaitable, Coroutine

T = TypeVar("T")

_loop: asyncio.AbstractEventLoop | None = None


def _get_loop() -> asyncio.AbstractEventLoop:
    global _loop
    if _loop is None or _loop.is_closed():
        _loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_loop)
    return _loop


def run_async[T](coro: Coroutine[object, object, T] | Awaitable[T]) -> T:
    loop = _get_loop()
    if loop.is_running():
        raise RuntimeError("run_async cannot be called from a running loop")
    return loop.run_until_complete(coro)  # type: ignore[arg-type]
