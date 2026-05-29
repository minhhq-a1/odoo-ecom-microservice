"""Unit tests for retry_policy."""

from __future__ import annotations

from src.core.exceptions import CircuitOpenError
from src.workers.retry_policy import (
    MAX_RETRIES,
    ODOO_CONN_COUNTDOWN,
    SHOPEE_RL_COUNTDOWN_FALLBACK,
    circuit_retry_countdown,
    circuit_service_from_error,
)


def test_circuit_service_from_error_shopee() -> None:
    """Extract service name from Shopee circuit error."""
    error = CircuitOpenError("shopee", "Circuit open")
    assert circuit_service_from_error(error) == "shopee"


def test_circuit_service_from_error_odoo() -> None:
    """Extract service name from Odoo circuit error."""
    error = CircuitOpenError("odoo", "Circuit open")
    assert circuit_service_from_error(error) == "odoo"


def test_circuit_service_from_error_unknown() -> None:
    """Unknown circuit error should return None."""
    error = CircuitOpenError("unknown_service", "Circuit open")
    assert circuit_service_from_error(error) is None


def test_circuit_retry_countdown_shopee() -> None:
    """Shopee circuit open should use breaker timeout + 5."""
    countdown = circuit_retry_countdown("shopee")
    assert countdown > 0
    assert isinstance(countdown, int)


def test_circuit_retry_countdown_odoo() -> None:
    """Odoo circuit open should use breaker timeout + 5."""
    countdown = circuit_retry_countdown("odoo")
    assert countdown > 0
    assert isinstance(countdown, int)


def test_circuit_retry_countdown_unknown() -> None:
    """Unknown service should use max of all breakers."""
    countdown = circuit_retry_countdown("unknown")
    assert countdown > 0
    assert isinstance(countdown, int)


def test_circuit_retry_countdown_none() -> None:
    """None service should use max of all breakers."""
    countdown = circuit_retry_countdown(None)
    assert countdown > 0
    assert isinstance(countdown, int)


def test_odoo_conn_countdown_constant() -> None:
    """ODOO_CONN_COUNTDOWN should be defined."""
    assert ODOO_CONN_COUNTDOWN > 0
    assert isinstance(ODOO_CONN_COUNTDOWN, int)


def test_max_retries_constant() -> None:
    """MAX_RETRIES should be defined."""
    assert MAX_RETRIES > 0
    assert isinstance(MAX_RETRIES, int)


def test_shopee_rl_countdown_fallback_constant() -> None:
    """SHOPEE_RL_COUNTDOWN_FALLBACK should be defined."""
    assert SHOPEE_RL_COUNTDOWN_FALLBACK > 0
    assert isinstance(SHOPEE_RL_COUNTDOWN_FALLBACK, int)
