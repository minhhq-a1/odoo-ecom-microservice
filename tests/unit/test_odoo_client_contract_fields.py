"""Unit tests for Odoo addon contract fields used by the middleware."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.odoo.client import OdooClient
from src.schemas.unified import UnifiedAddress


@pytest.mark.asyncio
async def test_update_order_tracking_writes_existing_order() -> None:
    client = OdooClient()
    client.search_read = AsyncMock(
        return_value=[{"id": 123, "x_tracking_number": ""}],
    )
    client.write = AsyncMock(return_value=True)

    updated = await client.update_order_tracking("shopee", "ORD-1", "SPXVN001")

    assert updated is True
    client.search_read.assert_awaited_once_with(
        "sale.order",
        [["x_platform", "=", "shopee"], ["x_platform_order_id", "=", "ORD-1"]],
        ["id", "x_tracking_number"],
        limit=1,
    )
    client.write.assert_awaited_once_with(
        "sale.order",
        [123],
        {"x_tracking_number": "SPXVN001"},
    )


@pytest.mark.asyncio
async def test_update_order_tracking_returns_false_when_order_missing() -> None:
    client = OdooClient()
    client.search_read = AsyncMock(return_value=[])
    client.write = AsyncMock()

    updated = await client.update_order_tracking("shopee", "ORD-404", "SPXVN404")

    assert updated is False
    client.write.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_or_create_partner_populates_platform_fields_on_create() -> None:
    client = OdooClient()
    client.search_read = AsyncMock(return_value=[])
    client._get_vietnam_id = AsyncMock(return_value=241)
    client.create = AsyncMock(return_value=77)

    address = UnifiedAddress(
        full_name="Nguyen Van A",
        phone="0901234567",
        address_line="123 Le Loi",
        district="Quan 1",
        province="TP HCM",
        ward="Ben Nghe",
    )

    partner_id = await client.get_or_create_partner(address, "shopee", "buyer-42")

    assert partner_id == 77
    values = client.create.await_args.args[1]
    assert values["x_platform_source"] == "shopee"
    assert values["x_platform_buyer_id"] == "buyer-42"


@pytest.mark.asyncio
async def test_get_or_create_partner_backfills_missing_platform_fields() -> None:
    client = OdooClient()
    client.search_read = AsyncMock(
        return_value=[
            {
                "id": 88,
                "name": "Nguyen Van A",
                "x_platform_source": False,
                "x_platform_buyer_id": False,
            }
        ],
    )
    client.write = AsyncMock(return_value=True)

    address = UnifiedAddress(
        full_name="Nguyen Van A",
        phone="0901234567",
        address_line="123 Le Loi",
        district="Quan 1",
        province="TP HCM",
    )

    partner_id = await client.get_or_create_partner(address, "shopee", "buyer-42")

    assert partner_id == 88
    client.write.assert_awaited_once_with(
        "res.partner",
        [88],
        {"x_platform_source": "shopee", "x_platform_buyer_id": "buyer-42"},
    )
