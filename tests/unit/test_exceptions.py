"""Unit tests for exceptions module."""

from __future__ import annotations

from src.core.exceptions import (
    CircuitOpenError,
    ConfigError,
    DuplicateOrderError,
    MiddlewareError,
    OdooConnectionError,
    OdooError,
    OdooPermissionError,
    OdooValidationError,
    PlatformError,
    ProductNotFoundError,
    ShopeeAuthError,
    ShopeeError,
    ShopeeRateLimitError,
    ShopeeSignatureError,
    WebhookReplayError,
)


def test_middleware_error() -> None:
    """MiddlewareError should store context."""
    error = MiddlewareError("Test error", key="value")
    assert "Test error" in str(error)
    assert error.context["key"] == "value"
    assert error.retryable is True


def test_config_error() -> None:
    """ConfigError should not be retryable."""
    error = ConfigError("Config error")
    assert error.retryable is False


def test_platform_error() -> None:
    """PlatformError should store platform name."""
    error = PlatformError("shopee", "Platform error")
    assert error.platform == "shopee"
    assert "Platform error" in str(error)


def test_shopee_error() -> None:
    """ShopeeError should inherit from PlatformError."""
    error = ShopeeError("shopee", "Shopee error")
    assert error.platform == "shopee"


def test_shopee_auth_error() -> None:
    """ShopeeAuthError should not be retryable."""
    error = ShopeeAuthError("shopee", "Auth failed")
    assert error.retryable is False


def test_shopee_rate_limit_error() -> None:
    """ShopeeRateLimitError should store retry_after."""
    error = ShopeeRateLimitError(retry_after=60)
    assert error.retry_after == 60
    assert error.platform == "shopee"
    assert "60s" in str(error)


def test_shopee_signature_error() -> None:
    """ShopeeSignatureError should not be retryable."""
    error = ShopeeSignatureError("shopee", "Invalid signature")
    assert error.retryable is False


def test_odoo_error() -> None:
    """OdooError should be retryable by default."""
    error = OdooError("Odoo error")
    assert error.retryable is True


def test_odoo_connection_error() -> None:
    """OdooConnectionError should be instantiable."""
    error = OdooConnectionError("Connection failed")
    assert "Connection failed" in str(error)


def test_odoo_validation_error() -> None:
    """OdooValidationError should not be retryable."""
    error = OdooValidationError("Invalid data")
    assert error.retryable is False


def test_odoo_permission_error() -> None:
    """OdooPermissionError should not be retryable."""
    error = OdooPermissionError("Permission denied")
    assert error.retryable is False


def test_product_not_found_error() -> None:
    """ProductNotFoundError should store SKU."""
    error = ProductNotFoundError("SKU-001")
    assert error.sku == "SKU-001"
    assert "SKU-001" in str(error)
    assert error.retryable is False


def test_duplicate_order_error() -> None:
    """DuplicateOrderError should not be retryable."""
    error = DuplicateOrderError("Duplicate order")
    assert error.retryable is False


def test_circuit_open_error() -> None:
    """CircuitOpenError should store service name."""
    error = CircuitOpenError("shopee", "Circuit is open")
    assert error.service == "shopee"
    assert "Circuit is open" in str(error)
    assert error.retryable is True


def test_webhook_replay_error() -> None:
    """WebhookReplayError should not be retryable."""
    error = WebhookReplayError("Replay detected")
    assert error.retryable is False
