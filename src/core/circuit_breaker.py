"""Async circuit breaker — PER-PROCESS, in-memory state.

State (CLOSED/OPEN/HALF_OPEN) lives in this worker process only; it is NOT
shared via Redis or any other store. Each Uvicorn/Celery worker keeps its own
breaker, so OPEN does not coordinate across the fleet. Size thresholds with
that in mind: the cluster-wide failure rate that trips protection is roughly
`failure_threshold x (number of worker processes)`. If cross-worker
coordination is ever required, back the state with Redis under `_lock`.
"""

from __future__ import annotations

import asyncio
import time
from enum import StrEnum
from typing import TYPE_CHECKING, TypeVar

from src.core.exceptions import CircuitOpenError
from src.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = get_logger(__name__)
T = TypeVar("T")


class CBState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """
    Rolling-window failure counter. Async-safe via internal lock.
    """

    def __init__(
        self,
        service: str,
        failure_threshold: int = 5,
        rolling_window_seconds: int = 60,
        open_timeout_seconds: int = 60,
        half_open_success_required: int = 1,
    ) -> None:
        self.service = service
        self.failure_threshold = failure_threshold
        self.rolling_window = rolling_window_seconds
        self.open_timeout = open_timeout_seconds
        self.half_open_success_required = half_open_success_required

        self._state: CBState = CBState.CLOSED
        self._failures: list[float] = []
        self._opened_at: float | None = None
        self._half_open_passes: int = 0
        self._lock = asyncio.Lock()

    @property
    def state(self) -> CBState:
        return self._state

    async def call(self, fn: Callable[..., Awaitable[T]], *args: object, **kwargs: object) -> T:
        async with self._lock:
            await self._maybe_transition_to_half_open()
            if self._state is CBState.OPEN:
                raise CircuitOpenError(self.service, f"Circuit {self.service} is OPEN")

        try:
            result = await fn(*args, **kwargs)
        except Exception:
            async with self._lock:
                self._record_failure()
            raise

        async with self._lock:
            self._record_success()
        return result

    async def _maybe_transition_to_half_open(self) -> None:
        if self._state is not CBState.OPEN or self._opened_at is None:
            return
        if time.time() - self._opened_at >= self.open_timeout:
            self._state = CBState.HALF_OPEN
            self._half_open_passes = 0
            logger.info("circuit_half_open", service=self.service)

    def _record_failure(self) -> None:
        now = time.time()
        self._failures = [t for t in self._failures if now - t < self.rolling_window]
        self._failures.append(now)

        if self._state is CBState.HALF_OPEN or len(self._failures) >= self.failure_threshold:
            self._open(now)

    def _record_success(self) -> None:
        if self._state is CBState.HALF_OPEN:
            self._half_open_passes += 1
            if self._half_open_passes >= self.half_open_success_required:
                self._close()
        elif self._state is CBState.CLOSED:
            self._failures.clear()

    def _open(self, now: float) -> None:
        self._state = CBState.OPEN
        self._opened_at = now
        logger.warning("circuit_open", service=self.service, failures=len(self._failures))

    def _close(self) -> None:
        self._state = CBState.CLOSED
        self._failures.clear()
        self._opened_at = None
        self._half_open_passes = 0
        logger.info("circuit_closed", service=self.service)


# Thresholds are PER PROCESS (see module docstring). Workers run
# --concurrency=8 across several containers plus the API processes, so the
# cluster-wide trip point is ~threshold x that process count. Kept low so a
# genuinely-down dependency is abandoned quickly per process instead of each
# process burning `threshold` calls against it.
odoo_breaker = CircuitBreaker(
    service="odoo",
    failure_threshold=3,
    rolling_window_seconds=60,
    open_timeout_seconds=60,
)

shopee_breaker = CircuitBreaker(
    service="shopee",
    failure_threshold=5,
    rolling_window_seconds=60,
    open_timeout_seconds=30,
)

BREAKER_REGISTRY: dict[str, CircuitBreaker] = {
    "odoo": odoo_breaker,
    "shopee": shopee_breaker,
}
