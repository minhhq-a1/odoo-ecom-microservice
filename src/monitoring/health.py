"""Health checks for /health and /ready."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text

from src.core.circuit_breaker import odoo_breaker, shopee_breaker
from src.core.database import get_async_db_context
from src.core.logging import get_logger
from src.core.redis import get_redis

logger = get_logger(__name__)


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str | None = None
    duration_ms: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ok": self.ok,
            "detail": self.detail,
            "duration_ms": self.duration_ms,
        }


async def _timed(name: str, coro) -> CheckResult:
    loop = asyncio.get_event_loop()
    start = loop.time()
    try:
        ok, detail = await coro
        return CheckResult(name, ok, detail, (loop.time() - start) * 1000)
    except Exception as e:
        return CheckResult(name, False, str(e), (loop.time() - start) * 1000)


async def _check_postgres() -> tuple[bool, str | None]:
    async with get_async_db_context() as db:
        await db.execute(text("SELECT 1"))
    return True, None


async def _check_redis() -> tuple[bool, str | None]:
    r = await get_redis()
    pong = await r.ping()
    return bool(pong), None


async def _check_outbox_lag() -> tuple[bool, str | None]:
    async with get_async_db_context() as db:
        result = await db.execute(
            text(
                "SELECT EXTRACT(EPOCH FROM (NOW() - MIN(process_after))) "
                "FROM webhook_outbox WHERE status IN ('pending', 'failed')"
            )
        )
        row = result.scalar()
        age_sec = float(row) if row is not None else 0
    return age_sec < 60, f"oldest_pending_age_sec={age_sec:.1f}"


async def check_all() -> dict[str, Any]:
    results = await asyncio.gather(
        _timed("postgres", _check_postgres()),
        _timed("redis", _check_redis()),
        _timed("outbox_lag", _check_outbox_lag()),
        return_exceptions=False,
    )
    base = {r.name: r.to_dict() for r in results}
    base["circuit_odoo"] = {
        "name": "circuit_odoo",
        "ok": odoo_breaker.state.value == "closed",
        "detail": odoo_breaker.state.value,
    }
    base["circuit_shopee"] = {
        "name": "circuit_shopee",
        "ok": shopee_breaker.state.value == "closed",
        "detail": shopee_breaker.state.value,
    }
    return base
