"""Shared retry policy for Celery workers — countdowns + exception classifiers.

Extracted to a neutral module so order_worker and stock_worker don't couple
through private names. Add new worker tasks here instead of importing private
helpers from peers.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.core.circuit_breaker import BREAKER_REGISTRY

if TYPE_CHECKING:
    from src.core.exceptions import CircuitOpenError

MAX_RETRIES = 8
ODOO_CONN_COUNTDOWN = 10
SHOPEE_RL_COUNTDOWN_FALLBACK = 30


def circuit_retry_countdown(service: str | None = None) -> int:
    """Wait until the named breaker is allowed to half-open again, + jitter.

    `service` is a key in BREAKER_REGISTRY ("shopee", "odoo", etc.) or None.
    For None or unknown services we wait the max of all registered breakers —
    conservative but only used when the service can't be identified.
    """
    if service and service in BREAKER_REGISTRY:
        return BREAKER_REGISTRY[service].open_timeout + 5
    return max(b.open_timeout for b in BREAKER_REGISTRY.values()) + 5


def circuit_service_from_error(e: CircuitOpenError) -> str | None:
    """Extract the breaker service name from CircuitOpenError.

    Returns the service attribute if present and registered, else None.
    Caller falls back to max-timeout for unknown services.
    """
    service = getattr(e, "service", None)
    if service and service in BREAKER_REGISTRY:
        return service
    return None
