"""Shared retry policy for Celery workers — countdowns + exception classifiers.

Extracted to a neutral module so order_worker and stock_worker don't couple
through private names. Add new worker tasks here instead of importing private
helpers from peers.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from src.core.circuit_breaker import odoo_breaker, shopee_breaker

if TYPE_CHECKING:
    from src.core.exceptions import CircuitOpenError

MAX_RETRIES = 8
ODOO_CONN_COUNTDOWN = 10
SHOPEE_RL_COUNTDOWN_FALLBACK = 30


def circuit_retry_countdown(service: str | None = None) -> int:
    """Wait until the named breaker is allowed to half-open again, + jitter.

    `service` is "shopee", "odoo", or None. For None we wait the longer of the
    two timeouts — conservative but only used when the breaker name can't be
    parsed out of the exception.
    """
    if service == "shopee":
        return shopee_breaker.open_timeout + 5
    if service == "odoo":
        return odoo_breaker.open_timeout + 5
    return max(odoo_breaker.open_timeout, shopee_breaker.open_timeout) + 5


def circuit_service_from_error(e: CircuitOpenError) -> str | None:
    """Parse the breaker service name out of `CircuitOpenError` message.

    The breaker raises `CircuitOpenError(f"Circuit {self.service} is OPEN")`
    where `self.service` is "odoo" or "shopee" today. Returns None for any
    future third-party breaker — caller falls back to the max-timeout.
    """
    msg = str(e).lower()
    if "shopee" in msg:
        return "shopee"
    if "odoo" in msg:
        return "odoo"
    return None
