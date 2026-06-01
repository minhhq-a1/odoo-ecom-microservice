"""Dry-run must never reach the wire (P1 review fix).

MIDDLEWARE_DRY_RUN gates every Odoo/marketplace write at the client
chokepoints; reads stay live. These tests assert the underlying transport
(_execute / _request) is never invoked for write paths in dry-run.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.connectors.shopee.client import ShopeeConnector
from src.odoo.client import OdooClient


@pytest.mark.asyncio
async def test_odoo_write_create_call_skip_in_dry_run() -> None:
    client = OdooClient()
    client._execute = AsyncMock()  # must NOT be called

    with patch("src.odoo.client.settings") as s:
        s.MIDDLEWARE_DRY_RUN = True
        assert await client.write("sale.order", [1], {"x_sync_status": "synced"}) is True
        assert await client.create("res.partner", {"name": "x"}) == -1
        assert await client.call_method("sale.order", "action_confirm", [1]) is True

    client._execute.assert_not_called()


@pytest.mark.asyncio
async def test_odoo_reads_stay_live_in_dry_run() -> None:
    client = OdooClient()
    client._execute = AsyncMock(return_value=[{"id": 9}])

    with patch("src.odoo.client.settings") as s:
        s.MIDDLEWARE_DRY_RUN = True
        await client.search_read("sale.order", [["id", "=", 9]], ["id"], limit=1)

    client._execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_shopee_writes_skip_in_dry_run() -> None:
    with (
        patch("src.connectors.shopee.client.settings") as s,
        patch("src.connectors.shopee.client.httpx.AsyncClient"),
    ):
        s.MIDDLEWARE_DRY_RUN = True
        s.SHOPEE_SHOP_ID = "1"
        s.SHOPEE_PARTNER_ID = "2"
        s.SHOPEE_PARTNER_KEY = "k"
        s.shopee_base_url = "https://x/api/v2"
        conn = ShopeeConnector()
        conn._request = AsyncMock()  # must NOT be called

        req = MagicMock(sku="123", quantity=5)
        assert await conn.update_stock(req) is None
        assert await conn.update_price("123:456", 1000.0) is None
        assert await conn.confirm_shipment("ORD-1", "SPX1") is None

    conn._request.assert_not_called()
