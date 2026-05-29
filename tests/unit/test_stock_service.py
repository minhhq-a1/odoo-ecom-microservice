"""Unit tests for stock_service."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.stock_service import StockService


@pytest.mark.asyncio
async def test_calculate_platform_stock_default_config() -> None:
    """Calculate stock with default config should apply default buffer and allocation."""
    mock_odoo = AsyncMock()
    mock_odoo.get_stock_quantity = AsyncMock(return_value=100)

    with (
        patch("src.services.stock_service.get_async_db_context") as mock_db_ctx,
        patch("src.services.stock_service.settings") as mock_settings,
    ):
        mock_settings.DEFAULT_STOCK_BUFFER_PCT = 10.0
        mock_settings.DEFAULT_SHOPEE_ALLOCATION_PCT = 80.0

        mock_db = AsyncMock()
        mock_result = AsyncMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=None)
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db_ctx.return_value.__aexit__ = AsyncMock()

        svc = StockService(odoo=mock_odoo)
        result = await svc.calculate_platform_stock("SKU-001", "shopee")

    # 100 * (1 - 0.1) * 0.8 = 72
    assert result == 72


@pytest.mark.asyncio
async def test_calculate_platform_stock_custom_config() -> None:
    """Calculate stock with custom config should use configured percentages."""
    mock_odoo = AsyncMock()
    mock_odoo.get_stock_quantity = AsyncMock(return_value=200)

    mock_config = MagicMock()
    mock_config.buffer_pct = 20.0
    mock_config.allocation_pct = 50.0

    with patch("src.services.stock_service.get_async_db_context") as mock_db_ctx:
        mock_db = AsyncMock()
        mock_result = AsyncMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=mock_config)
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db_ctx.return_value.__aexit__ = AsyncMock()

        svc = StockService(odoo=mock_odoo)
        result = await svc.calculate_platform_stock("SKU-001", "shopee")

    # 200 * (1 - 0.2) * 0.5 = 80
    assert result == 80


@pytest.mark.asyncio
async def test_calculate_platform_stock_never_negative() -> None:
    """Calculate stock should never return negative values."""
    mock_odoo = AsyncMock()
    mock_odoo.get_stock_quantity = AsyncMock(return_value=5)

    with (
        patch("src.services.stock_service.get_async_db_context") as mock_db_ctx,
        patch("src.services.stock_service.settings") as mock_settings,
    ):
        mock_settings.DEFAULT_STOCK_BUFFER_PCT = 50.0
        mock_settings.DEFAULT_SHOPEE_ALLOCATION_PCT = 10.0

        mock_db = AsyncMock()
        mock_result = AsyncMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=None)
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db_ctx.return_value.__aexit__ = AsyncMock()

        svc = StockService(odoo=mock_odoo)
        result = await svc.calculate_platform_stock("SKU-001", "shopee")

    # 5 * 0.5 * 0.1 = 0.25 → int(0.25) = 0, max(0, 0) = 0
    assert result == 0


@pytest.mark.asyncio
async def test_calculate_platform_stock_zero_odoo_stock() -> None:
    """Calculate stock with zero Odoo stock should return zero."""
    mock_odoo = AsyncMock()
    mock_odoo.get_stock_quantity = AsyncMock(return_value=0)

    with patch("src.services.stock_service.get_async_db_context") as mock_db_ctx:
        mock_db = AsyncMock()
        mock_result = AsyncMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=None)
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db_ctx.return_value.__aexit__ = AsyncMock()

        svc = StockService(odoo=mock_odoo)
        result = await svc.calculate_platform_stock("SKU-001", "shopee")

    assert result == 0


@pytest.mark.asyncio
async def test_calculate_bundle_stock_no_mapping() -> None:
    """Calculate bundle stock with no mapping should return zero."""
    with patch("src.services.stock_service.get_async_db_context") as mock_db_ctx:
        mock_db = AsyncMock()
        mock_result = AsyncMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=None)
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db_ctx.return_value.__aexit__ = AsyncMock()

        svc = StockService()
        result = await svc.calculate_bundle_stock("UNKNOWN", "shopee")

    assert result == 0


@pytest.mark.asyncio
async def test_calculate_bundle_stock_simple_mapping() -> None:
    """Calculate bundle stock for simple mapping should use calculate_platform_stock."""
    mock_mapping = MagicMock()
    mock_mapping.mapping_type = "simple"
    mock_mapping.odoo_sku = "SKU-001"

    mock_odoo = AsyncMock()
    mock_odoo.get_stock_quantity = AsyncMock(return_value=100)

    with (
        patch("src.services.stock_service.get_async_db_context") as mock_db_ctx,
        patch("src.services.stock_service.settings") as mock_settings,
    ):
        mock_settings.DEFAULT_STOCK_BUFFER_PCT = 10.0
        mock_settings.DEFAULT_SHOPEE_ALLOCATION_PCT = 80.0

        mock_db = AsyncMock()
        mock_result1 = AsyncMock()
        mock_result1.scalar_one_or_none = MagicMock(return_value=mock_mapping)
        mock_result2 = AsyncMock()
        mock_result2.scalar_one_or_none = MagicMock(return_value=None)
        mock_db.execute = AsyncMock(side_effect=[mock_result1, mock_result2])
        mock_db_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db_ctx.return_value.__aexit__ = AsyncMock()

        svc = StockService(odoo=mock_odoo)
        result = await svc.calculate_bundle_stock("SKU-001", "shopee")

    assert result == 72


@pytest.mark.asyncio
async def test_calculate_bundle_stock_no_components() -> None:
    """Calculate bundle stock with no components should return zero."""
    mock_mapping = MagicMock()
    mock_mapping.id = 1
    mock_mapping.mapping_type = "bundle"

    with patch("src.services.stock_service.get_async_db_context") as mock_db_ctx:
        mock_db = AsyncMock()
        mock_result1 = AsyncMock()
        mock_result1.scalar_one_or_none = MagicMock(return_value=mock_mapping)
        mock_result2 = AsyncMock()
        mock_result2.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
        mock_db.execute = AsyncMock(side_effect=[mock_result1, mock_result2])
        mock_db_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db_ctx.return_value.__aexit__ = AsyncMock()

        svc = StockService()
        result = await svc.calculate_bundle_stock("BUNDLE-001", "shopee")

    assert result == 0


@pytest.mark.asyncio
async def test_calculate_bundle_stock_min_component() -> None:
    """Calculate bundle stock should return min(allocated // qty) across components."""
    mock_mapping = MagicMock()
    mock_mapping.id = 1
    mock_mapping.mapping_type = "bundle"

    mock_comp1 = MagicMock()
    mock_comp1.odoo_sku = "COMP-A"
    mock_comp1.quantity = 2

    mock_comp2 = MagicMock()
    mock_comp2.odoo_sku = "COMP-B"
    mock_comp2.quantity = 1

    mock_odoo = AsyncMock()
    # COMP-A: 100 allocated → 100 // 2 = 50 bundles
    # COMP-B: 30 allocated → 30 // 1 = 30 bundles
    # min(50, 30) = 30
    mock_odoo.get_stock_quantity = AsyncMock(side_effect=[100, 30])

    with (
        patch("src.services.stock_service.get_async_db_context") as mock_db_ctx,
        patch("src.services.stock_service.settings") as mock_settings,
    ):
        mock_settings.DEFAULT_STOCK_BUFFER_PCT = 0.0
        mock_settings.DEFAULT_SHOPEE_ALLOCATION_PCT = 100.0

        mock_db = AsyncMock()
        mock_result1 = AsyncMock()
        mock_result1.scalar_one_or_none = MagicMock(return_value=mock_mapping)
        mock_result2 = AsyncMock()
        mock_result2.scalars = MagicMock(
            return_value=MagicMock(all=MagicMock(return_value=[mock_comp1, mock_comp2]))
        )
        # For calculate_platform_stock calls
        mock_result3 = AsyncMock()
        mock_result3.scalar_one_or_none = MagicMock(return_value=None)
        mock_result4 = AsyncMock()
        mock_result4.scalar_one_or_none = MagicMock(return_value=None)
        mock_db.execute = AsyncMock(
            side_effect=[mock_result1, mock_result2, mock_result3, mock_result4]
        )
        mock_db_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db_ctx.return_value.__aexit__ = AsyncMock()

        svc = StockService(odoo=mock_odoo)
        result = await svc.calculate_bundle_stock("BUNDLE-001", "shopee")

    assert result == 30


@pytest.mark.asyncio
async def test_calculate_bundle_stock_zero_quantity_component() -> None:
    """Calculate bundle stock should handle zero quantity components safely."""
    mock_mapping = MagicMock()
    mock_mapping.id = 1
    mock_mapping.mapping_type = "bundle"

    mock_comp = MagicMock()
    mock_comp.odoo_sku = "COMP-A"
    mock_comp.quantity = 0  # Edge case

    mock_odoo = AsyncMock()
    mock_odoo.get_stock_quantity = AsyncMock(return_value=100)

    with (
        patch("src.services.stock_service.get_async_db_context") as mock_db_ctx,
        patch("src.services.stock_service.settings") as mock_settings,
    ):
        mock_settings.DEFAULT_STOCK_BUFFER_PCT = 0.0
        mock_settings.DEFAULT_SHOPEE_ALLOCATION_PCT = 100.0

        mock_db = AsyncMock()
        mock_result1 = AsyncMock()
        mock_result1.scalar_one_or_none = MagicMock(return_value=mock_mapping)
        mock_result2 = AsyncMock()
        mock_result2.scalars = MagicMock(
            return_value=MagicMock(all=MagicMock(return_value=[mock_comp]))
        )
        mock_result3 = AsyncMock()
        mock_result3.scalar_one_or_none = MagicMock(return_value=None)
        mock_db.execute = AsyncMock(side_effect=[mock_result1, mock_result2, mock_result3])
        mock_db_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db_ctx.return_value.__aexit__ = AsyncMock()

        svc = StockService(odoo=mock_odoo)
        result = await svc.calculate_bundle_stock("BUNDLE-001", "shopee")

    # 100 // max(1, 0) = 100 // 1 = 100
    assert result == 100
