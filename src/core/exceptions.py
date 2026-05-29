"""Base exception hierarchy."""
from __future__ import annotations


class MiddlewareError(Exception):
    """Base for all middleware errors."""

    retryable: bool = True

    def __init__(self, message: str = "", **context: object) -> None:
        super().__init__(message)
        self.context = context


class ConfigError(MiddlewareError):
    retryable = False


class PlatformError(MiddlewareError):
    def __init__(self, platform: str, message: str = "", **context: object) -> None:
        super().__init__(message, **context)
        self.platform = platform


class ShopeeError(PlatformError):
    pass


class ShopeeAuthError(ShopeeError):
    retryable = False


class ShopeeRateLimitError(ShopeeError):
    def __init__(self, retry_after: int = 60, **context: object) -> None:
        super().__init__(platform="shopee",
                         message=f"Rate limited, retry after {retry_after}s",
                         **context)
        self.retry_after = retry_after


class ShopeeSignatureError(ShopeeError):
    retryable = False


class OdooError(MiddlewareError):
    pass


class OdooConnectionError(OdooError):
    pass


class OdooValidationError(OdooError):
    retryable = False


class OdooPermissionError(OdooError):
    retryable = False


class ProductNotFoundError(MiddlewareError):
    retryable = False

    def __init__(self, sku: str) -> None:
        super().__init__(f"SKU not found in Odoo: {sku}", sku=sku)
        self.sku = sku


class DuplicateOrderError(MiddlewareError):
    retryable = False


class CircuitOpenError(MiddlewareError):
    retryable = True


class WebhookReplayError(MiddlewareError):
    retryable = False
