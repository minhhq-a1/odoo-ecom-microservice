"""Unit tests for mapping_service."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.mapping_service import MappingService


@pytest.mark.asyncio
async def test_get_by_platform_sku_cache_hit(redis: AsyncMock) -> None:
    """Cache hit should return cached data without DB query."""
    cached_data = {
        "id": 1,
        "platform_product_id": "PROD-001",
        "platform_sku_id": "SKU-001",
        "odoo_product_id": 100,
        "odoo_sku": "ODOO-001",
        "mapping_type": "simple",
    }
    redis.get = AsyncMock(return_value=json.dumps(cached_data))

    with patch("src.services.mapping_service.get_redis", return_value=redis):
        result = await MappingService.get_by_platform_sku("shopee", "SKU-001")

    assert result == cached_data
    redis.get.assert_called_once_with("mapping:platform_sku:shopee:SKU-001")


@pytest.mark.asyncio
async def test_get_by_platform_sku_cache_miss(redis: AsyncMock) -> None:
    """Cache miss should query DB and cache result."""
    redis.get = AsyncMock(return_value=None)
    redis.setex = AsyncMock()

    mock_mapping = MagicMock()
    mock_mapping.id = 1
    mock_mapping.platform_product_id = "PROD-001"
    mock_mapping.platform_sku_id = "SKU-001"
    mock_mapping.odoo_product_id = 100
    mock_mapping.odoo_sku = "ODOO-001"
    mock_mapping.mapping_type = "simple"

    with (
        patch("src.services.mapping_service.get_redis", return_value=redis),
        patch("src.services.mapping_service.get_async_db_context") as mock_db_ctx,
    ):
        mock_db = AsyncMock()
        mock_result = AsyncMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=mock_mapping)
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db_ctx.return_value.__aexit__ = AsyncMock()

        result = await MappingService.get_by_platform_sku("shopee", "SKU-001")

    assert result["id"] == 1
    assert result["platform_sku_id"] == "SKU-001"
    assert result["odoo_sku"] == "ODOO-001"
    redis.setex.assert_called_once()


@pytest.mark.asyncio
async def test_get_by_platform_sku_not_found(redis: AsyncMock) -> None:
    """Not found should return None."""
    redis.get = AsyncMock(return_value=None)

    with (
        patch("src.services.mapping_service.get_redis", return_value=redis),
        patch("src.services.mapping_service.get_async_db_context") as mock_db_ctx,
    ):
        mock_db = AsyncMock()
        mock_result = AsyncMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=None)
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db_ctx.return_value.__aexit__ = AsyncMock()

        result = await MappingService.get_by_platform_sku("shopee", "UNKNOWN")

    assert result is None


@pytest.mark.asyncio
async def test_list_active_for_platform() -> None:
    """List active mappings should return all active mappings for platform."""
    mock_mapping1 = MagicMock()
    mock_mapping1.id = 1
    mock_mapping1.platform_product_id = "PROD-001"
    mock_mapping1.platform_sku_id = "SKU-001"
    mock_mapping1.odoo_product_id = 100
    mock_mapping1.odoo_sku = "ODOO-001"
    mock_mapping1.mapping_type = "simple"

    mock_mapping2 = MagicMock()
    mock_mapping2.id = 2
    mock_mapping2.platform_product_id = "PROD-002"
    mock_mapping2.platform_sku_id = "SKU-002"
    mock_mapping2.odoo_product_id = 200
    mock_mapping2.odoo_sku = "ODOO-002"
    mock_mapping2.mapping_type = "bundle"

    with patch("src.services.mapping_service.get_async_db_context") as mock_db_ctx:
        mock_db = AsyncMock()
        mock_result = AsyncMock()
        mock_result.scalars = MagicMock(
            return_value=MagicMock(all=MagicMock(return_value=[mock_mapping1, mock_mapping2]))
        )
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db_ctx.return_value.__aexit__ = AsyncMock()

        result = await MappingService.list_active_for_platform("shopee")

    assert len(result) == 2
    assert result[0]["id"] == 1
    assert result[1]["id"] == 2


@pytest.mark.asyncio
async def test_get_bundle_components_cache_hit(redis: AsyncMock) -> None:
    """Cache hit should return cached components."""
    cached_data = [
        {"odoo_sku": "COMP-A", "quantity": 2},
        {"odoo_sku": "COMP-B", "quantity": 1},
    ]
    redis.get = AsyncMock(return_value=json.dumps(cached_data))

    with patch("src.services.mapping_service.get_redis", return_value=redis):
        result = await MappingService.get_bundle_components(1)

    assert result == cached_data
    redis.get.assert_called_once_with("mapping:bundle:1")


@pytest.mark.asyncio
async def test_get_bundle_components_cache_miss(redis: AsyncMock) -> None:
    """Cache miss should query DB and cache components."""
    redis.get = AsyncMock(return_value=None)
    redis.setex = AsyncMock()

    mock_comp1 = MagicMock()
    mock_comp1.odoo_sku = "COMP-A"
    mock_comp1.quantity = 2

    mock_comp2 = MagicMock()
    mock_comp2.odoo_sku = "COMP-B"
    mock_comp2.quantity = 1

    with (
        patch("src.services.mapping_service.get_redis", return_value=redis),
        patch("src.services.mapping_service.get_async_db_context") as mock_db_ctx,
    ):
        mock_db = AsyncMock()
        mock_result = AsyncMock()
        mock_result.scalars = MagicMock(
            return_value=MagicMock(all=MagicMock(return_value=[mock_comp1, mock_comp2]))
        )
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db_ctx.return_value.__aexit__ = AsyncMock()

        result = await MappingService.get_bundle_components(1)

    assert len(result) == 2
    assert result[0]["odoo_sku"] == "COMP-A"
    assert result[0]["quantity"] == 2
    redis.setex.assert_called_once()


@pytest.mark.asyncio
async def test_find_mappings_using_component_direct() -> None:
    """Find mappings should return direct mappings where odoo_sku matches."""
    mock_mapping = MagicMock()
    mock_mapping.id = 1
    mock_mapping.platform_product_id = "PROD-001"
    mock_mapping.platform_sku_id = "SKU-001"
    mock_mapping.odoo_sku = "COMP-A"
    mock_mapping.mapping_type = "simple"

    with patch("src.services.mapping_service.get_async_db_context") as mock_db_ctx:
        mock_db = AsyncMock()
        mock_result1 = AsyncMock()
        mock_result1.scalars = MagicMock(
            return_value=MagicMock(all=MagicMock(return_value=[mock_mapping]))
        )
        mock_result2 = AsyncMock()
        mock_result2.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
        mock_db.execute = AsyncMock(side_effect=[mock_result1, mock_result2])
        mock_db_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db_ctx.return_value.__aexit__ = AsyncMock()

        result = await MappingService.find_mappings_using_component("shopee", "COMP-A")

    assert len(result) == 1
    assert result[0]["odoo_sku"] == "COMP-A"


@pytest.mark.asyncio
async def test_find_mappings_using_component_bundle() -> None:
    """Find mappings should return bundle mappings that use the component."""
    mock_bundle = MagicMock()
    mock_bundle.id = 2
    mock_bundle.platform_product_id = "PROD-002"
    mock_bundle.platform_sku_id = "BUNDLE-001"
    mock_bundle.odoo_sku = "BUNDLE-SKU"
    mock_bundle.mapping_type = "bundle"

    with patch("src.services.mapping_service.get_async_db_context") as mock_db_ctx:
        mock_db = AsyncMock()
        mock_result1 = AsyncMock()
        mock_result1.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
        mock_result2 = AsyncMock()
        mock_result2.scalars = MagicMock(
            return_value=MagicMock(all=MagicMock(return_value=[mock_bundle]))
        )
        mock_db.execute = AsyncMock(side_effect=[mock_result1, mock_result2])
        mock_db_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db_ctx.return_value.__aexit__ = AsyncMock()

        result = await MappingService.find_mappings_using_component("shopee", "COMP-A")

    assert len(result) == 1
    assert result[0]["mapping_type"] == "bundle"


@pytest.mark.asyncio
async def test_find_mappings_using_component_deduplicates() -> None:
    """Find mappings should deduplicate results."""
    mock_mapping = MagicMock()
    mock_mapping.id = 1
    mock_mapping.platform_product_id = "PROD-001"
    mock_mapping.platform_sku_id = "SKU-001"
    mock_mapping.odoo_sku = "COMP-A"
    mock_mapping.mapping_type = "simple"

    with patch("src.services.mapping_service.get_async_db_context") as mock_db_ctx:
        mock_db = AsyncMock()
        # Return same mapping in both queries
        mock_result1 = AsyncMock()
        mock_result1.scalars = MagicMock(
            return_value=MagicMock(all=MagicMock(return_value=[mock_mapping]))
        )
        mock_result2 = AsyncMock()
        mock_result2.scalars = MagicMock(
            return_value=MagicMock(all=MagicMock(return_value=[mock_mapping]))
        )
        mock_db.execute = AsyncMock(side_effect=[mock_result1, mock_result2])
        mock_db_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db_ctx.return_value.__aexit__ = AsyncMock()

        result = await MappingService.find_mappings_using_component("shopee", "COMP-A")

    assert len(result) == 1  # Deduplicated


@pytest.mark.asyncio
async def test_invalidate(redis: AsyncMock) -> None:
    """Invalidate should delete cache key."""
    redis.delete = AsyncMock()

    with patch("src.services.mapping_service.get_redis", return_value=redis):
        await MappingService.invalidate("shopee", "SKU-001")

    redis.delete.assert_called_once_with("mapping:platform_sku:shopee:SKU-001")
