"""Unit tests for monitoring metrics."""

from __future__ import annotations

from src.monitoring.metrics import (
    CB_OPEN_TOTAL,
    CB_STATE,
    ODOO_DURATION,
    ODOO_REQUEST,
    SHOPEE_REQUEST,
    WEBHOOK_DURATION,
    WEBHOOK_RECEIVED,
    WEBHOOK_SIGNATURE_INVALID,
)


def test_webhook_received_exists() -> None:
    """WEBHOOK_RECEIVED should be defined."""
    assert WEBHOOK_RECEIVED is not None
    assert hasattr(WEBHOOK_RECEIVED, "labels")


def test_webhook_signature_invalid_exists() -> None:
    """WEBHOOK_SIGNATURE_INVALID should be defined."""
    assert WEBHOOK_SIGNATURE_INVALID is not None
    assert hasattr(WEBHOOK_SIGNATURE_INVALID, "labels")


def test_webhook_duration_exists() -> None:
    """WEBHOOK_DURATION should be defined."""
    assert WEBHOOK_DURATION is not None
    assert hasattr(WEBHOOK_DURATION, "labels")


def test_odoo_request_exists() -> None:
    """ODOO_REQUEST should be defined."""
    assert ODOO_REQUEST is not None
    assert hasattr(ODOO_REQUEST, "labels")


def test_odoo_duration_exists() -> None:
    """ODOO_DURATION should be defined."""
    assert ODOO_DURATION is not None
    assert hasattr(ODOO_DURATION, "labels")


def test_cb_state_exists() -> None:
    """CB_STATE should be defined."""
    assert CB_STATE is not None
    assert hasattr(CB_STATE, "labels")


def test_cb_open_total_exists() -> None:
    """CB_OPEN_TOTAL should be defined."""
    assert CB_OPEN_TOTAL is not None
    assert hasattr(CB_OPEN_TOTAL, "labels")


def test_shopee_request_exists() -> None:
    """SHOPEE_REQUEST should be defined."""
    assert SHOPEE_REQUEST is not None
    assert hasattr(SHOPEE_REQUEST, "labels")


def test_webhook_received_increment() -> None:
    """WEBHOOK_RECEIVED should be incrementable."""
    initial = WEBHOOK_RECEIVED.labels(platform="shopee", event_type="order_update")._value.get()
    WEBHOOK_RECEIVED.labels(platform="shopee", event_type="order_update").inc()
    after = WEBHOOK_RECEIVED.labels(platform="shopee", event_type="order_update")._value.get()
    assert after > initial


def test_cb_state_set() -> None:
    """CB_STATE should be settable."""
    CB_STATE.labels(service="test_service").set(1)
    value = CB_STATE.labels(service="test_service")._value.get()
    assert value == 1
