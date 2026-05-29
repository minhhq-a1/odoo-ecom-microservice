"""Unit tests for config module."""

from __future__ import annotations

from src.core.config import settings


def test_settings_environment() -> None:
    """Settings should have ENVIRONMENT defined."""
    assert settings.ENVIRONMENT in ("development", "staging", "production", "test")


def test_settings_log_level() -> None:
    """Settings should have LOG_LEVEL defined."""
    assert settings.LOG_LEVEL in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


def test_settings_secret_key() -> None:
    """Settings should have SECRET_KEY defined."""
    assert settings.SECRET_KEY is not None
    assert len(settings.SECRET_KEY) > 0


def test_settings_admin_secret_token() -> None:
    """Settings should have ADMIN_SECRET_TOKEN defined."""
    assert settings.ADMIN_SECRET_TOKEN is not None
    assert len(settings.ADMIN_SECRET_TOKEN) > 0


def test_settings_middleware_dry_run() -> None:
    """Settings should have MIDDLEWARE_DRY_RUN defined."""
    assert isinstance(settings.MIDDLEWARE_DRY_RUN, bool)


def test_settings_database_url() -> None:
    """Settings should have DATABASE_URL defined."""
    assert settings.DATABASE_URL is not None


def test_settings_redis_url() -> None:
    """Settings should have REDIS_URL defined."""
    assert settings.REDIS_URL is not None


def test_settings_odoo_url() -> None:
    """Settings should have ODOO_URL defined."""
    assert settings.ODOO_URL is not None


def test_settings_shopee_partner_id() -> None:
    """Settings should have SHOPEE_PARTNER_ID defined."""
    assert settings.SHOPEE_PARTNER_ID is not None


def test_settings_enable_stock_sync() -> None:
    """Settings should have ENABLE_STOCK_SYNC defined."""
    assert isinstance(settings.ENABLE_STOCK_SYNC, bool)


def test_settings_enable_price_sync() -> None:
    """Settings should have ENABLE_PRICE_SYNC defined."""
    assert isinstance(settings.ENABLE_PRICE_SYNC, bool)


def test_settings_default_stock_buffer_pct() -> None:
    """Settings should have DEFAULT_STOCK_BUFFER_PCT defined."""
    assert settings.DEFAULT_STOCK_BUFFER_PCT >= 0
    assert settings.DEFAULT_STOCK_BUFFER_PCT <= 100


def test_settings_default_shopee_allocation_pct() -> None:
    """Settings should have DEFAULT_SHOPEE_ALLOCATION_PCT defined."""
    assert settings.DEFAULT_SHOPEE_ALLOCATION_PCT >= 0
    assert settings.DEFAULT_SHOPEE_ALLOCATION_PCT <= 100
