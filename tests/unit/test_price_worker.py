"""Unit tests for price_worker - simplified to avoid async helper complexity."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from src.workers.price_worker import sync_price_for_sku


def test_sync_price_disabled() -> None:
    """Price sync should return disabled when ENABLE_PRICE_SYNC is False."""
    with patch("src.workers.price_worker.settings") as mock_settings:
        mock_settings.ENABLE_PRICE_SYNC = False

        result = sync_price_for_sku("SKU-001", platform="shopee")

        assert result["status"] == "disabled"


def test_sync_price_blocked_by_odoo_product() -> None:
    """Odoo addon block flag prevents marketplace price publication."""
    with patch("src.workers.price_worker.settings") as mock_settings:
        mock_settings.ENABLE_PRICE_SYNC = True
        mock_settings.MIDDLEWARE_DRY_RUN = False

        with patch("src.odoo.client.OdooClient") as mock_odoo_cls:
            mock_odoo = MagicMock()
            mock_odoo.get_product_marketplace_controls = AsyncMock(
                return_value={
                    "lst_price": 100000.0,
                    "x_block_marketplace_sync": True,
                },
            )
            mock_odoo_cls.return_value = mock_odoo

            result = sync_price_for_sku("SKU-001", platform="shopee")

    assert result == {"status": "blocked", "sku": "SKU-001"}
