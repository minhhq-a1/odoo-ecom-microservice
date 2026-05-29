"""Unit tests for `_build_order_lines` bundle Decimal split (Round 21 P2-21B)."""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from src.odoo.client import OdooClient
from src.schemas.unified import UnifiedOrderItem


def _item(qty: int = 1, discounted_price: Decimal = Decimal("100000")) -> UnifiedOrderItem:
    return UnifiedOrderItem(
        platform_item_id="ITEM-1",
        sku="BUNDLE-1",
        product_name="Bundle Combo",
        quantity=qty,
        unit_price=discounted_price,
        discounted_price=discounted_price,
        discount_amount=Decimal("0"),
        platform_variant_id=None,
        variant_name=None,
    )


@pytest.mark.asyncio
async def test_bundle_split_reconciles_subtotal_within_one_unit():
    """3-component bundle with odd qty split: sum(price*qty) drift ≤ 1 VND."""
    item = _item(qty=1, discounted_price=Decimal("100000"))
    components = [
        {"odoo_sku": "C1", "quantity": 1},
        {"odoo_sku": "C2", "quantity": 2},
        {"odoo_sku": "C3", "quantity": 3},
    ]
    mapping = {"id": 7, "mapping_type": "bundle", "odoo_sku": "BUNDLE-1"}

    client = OdooClient()
    with (
        patch("src.services.mapping_service.MappingService.get_by_platform_sku",
              new=AsyncMock(return_value=mapping)),
        patch("src.services.mapping_service.MappingService.get_bundle_components",
              new=AsyncMock(return_value=components)),
        patch.object(client, "get_product_id_by_sku",
                     new=AsyncMock(side_effect=lambda sku: hash(sku) & 0xFFFF)),
    ):
        lines = await client._build_order_lines([item], platform="shopee")

    assert len(lines) == 3
    subtotal = sum(Decimal(str(ln[2]["price_unit"])) * Decimal(ln[2]["product_uom_qty"])
                   for ln in lines)
    expected = item.discounted_price * Decimal(item.quantity)
    drift = abs(subtotal - expected)
    assert drift <= Decimal("1"), f"drift={drift} subtotal={subtotal} expected={expected}"


@pytest.mark.asyncio
async def test_bundle_split_even_division_zero_drift():
    """Even-split bundle (4 components, divisible) → zero drift."""
    item = _item(qty=1, discounted_price=Decimal("100000"))
    components = [{"odoo_sku": f"C{i}", "quantity": 1} for i in range(4)]
    mapping = {"id": 8, "mapping_type": "bundle", "odoo_sku": "BUNDLE-2"}

    client = OdooClient()
    with (
        patch("src.services.mapping_service.MappingService.get_by_platform_sku",
              new=AsyncMock(return_value=mapping)),
        patch("src.services.mapping_service.MappingService.get_bundle_components",
              new=AsyncMock(return_value=components)),
        patch.object(client, "get_product_id_by_sku",
                     new=AsyncMock(side_effect=lambda sku: 100)),
    ):
        lines = await client._build_order_lines([item], platform="shopee")

    subtotal = sum(Decimal(str(ln[2]["price_unit"])) * Decimal(ln[2]["product_uom_qty"])
                   for ln in lines)
    assert subtotal == Decimal("100000")
    # All 4 components share equal qty=1 → each line price = 25000.
    assert all(Decimal(str(ln[2]["price_unit"])) == Decimal("25000") for ln in lines)


@pytest.mark.asyncio
async def test_bundle_split_quantity_multiplier_scales_subtotal():
    """item.quantity=3 → subtotal = discounted_price * 3 within 1 VND."""
    item = _item(qty=3, discounted_price=Decimal("99999"))
    components = [
        {"odoo_sku": "C1", "quantity": 1},
        {"odoo_sku": "C2", "quantity": 2},
    ]
    mapping = {"id": 9, "mapping_type": "bundle", "odoo_sku": "BUNDLE-3"}

    client = OdooClient()
    with (
        patch("src.services.mapping_service.MappingService.get_by_platform_sku",
              new=AsyncMock(return_value=mapping)),
        patch("src.services.mapping_service.MappingService.get_bundle_components",
              new=AsyncMock(return_value=components)),
        patch.object(client, "get_product_id_by_sku",
                     new=AsyncMock(side_effect=lambda sku: 200)),
    ):
        lines = await client._build_order_lines([item], platform="shopee")

    subtotal = sum(Decimal(str(ln[2]["price_unit"])) * Decimal(ln[2]["product_uom_qty"])
                   for ln in lines)
    expected = item.discounted_price * Decimal(item.quantity)
    drift = abs(subtotal - expected)
    assert drift <= Decimal("1"), f"drift={drift} subtotal={subtotal} expected={expected}"
    # Qty: comp1 = 1*3 = 3, comp2 = 2*3 = 6.
    qtys = sorted(ln[2]["product_uom_qty"] for ln in lines)
    assert qtys == [3, 6]


@pytest.mark.asyncio
async def test_bundle_split_last_component_splits_quantity_to_bound_drift():
    """Last component may need two price buckets when qty amplifies rounding."""
    item = _item(qty=2, discounted_price=Decimal("1"))
    components = [
        {"odoo_sku": "C1", "quantity": 1},
        {"odoo_sku": "C2", "quantity": 2},
    ]
    mapping = {"id": 11, "mapping_type": "bundle", "odoo_sku": "BUNDLE-4"}

    client = OdooClient()
    with (
        patch("src.services.mapping_service.MappingService.get_by_platform_sku",
              new=AsyncMock(return_value=mapping)),
        patch("src.services.mapping_service.MappingService.get_bundle_components",
              new=AsyncMock(return_value=components)),
        patch.object(client, "get_product_id_by_sku",
                     new=AsyncMock(return_value=200)),
    ):
        lines = await client._build_order_lines([item], platform="shopee")

    subtotal = sum(Decimal(str(ln[2]["price_unit"])) * Decimal(ln[2]["product_uom_qty"])
                   for ln in lines)
    assert abs(subtotal - Decimal("2")) <= Decimal("1")
    assert any(ln[2]["product_uom_qty"] < 4 for ln in lines if "[bundle:C2]" in ln[2]["name"])


@pytest.mark.asyncio
async def test_bundle_empty_components_fallback_to_platform_sku():
    """Empty components list logs warning + falls back to mapping odoo_sku."""
    item = _item(qty=2, discounted_price=Decimal("50000"))
    mapping = {"id": 10, "mapping_type": "bundle", "odoo_sku": "BUNDLE-FALLBACK"}

    client = OdooClient()
    with (
        patch("src.services.mapping_service.MappingService.get_by_platform_sku",
              new=AsyncMock(return_value=mapping)),
        patch("src.services.mapping_service.MappingService.get_bundle_components",
              new=AsyncMock(return_value=[])),
        patch.object(client, "get_product_id_by_sku",
                     new=AsyncMock(return_value=999)),
    ):
        lines = await client._build_order_lines([item], platform="shopee")

    assert len(lines) == 1
    assert lines[0][2]["product_uom_qty"] == 2
    # Fallback uses single component with quantity=1 → price = full discounted_price.
    assert Decimal(str(lines[0][2]["price_unit"])) == Decimal("50000")
