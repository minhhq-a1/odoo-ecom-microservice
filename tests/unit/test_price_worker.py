"""Unit tests for price_worker - simplified to avoid async helper complexity."""

from __future__ import annotations

from unittest.mock import patch

from src.workers.price_worker import sync_price_for_sku


def test_sync_price_disabled() -> None:
    """Price sync should return disabled when ENABLE_PRICE_SYNC is False."""
    with patch("src.workers.price_worker.settings") as mock_settings:
        mock_settings.ENABLE_PRICE_SYNC = False

        result = sync_price_for_sku("SKU-001", platform="shopee")

        assert result["status"] == "disabled"
