"""Root pytest fixtures."""

from __future__ import annotations

import asyncio
import contextlib
import os
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
from fakeredis.aioredis import FakeRedis

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MIDDLEWARE_DRY_RUN", "true")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost/test")
os.environ.setdefault("SYNC_DATABASE_URL", "postgresql://postgres:postgres@localhost/test")
os.environ.setdefault("ODOO_URL", "http://odoo.test")
os.environ.setdefault("ODOO_DB", "test")
os.environ.setdefault("ODOO_USER", "test")
os.environ.setdefault("ODOO_PASSWORD", "test")
os.environ.setdefault("SHOPEE_PARTNER_ID", "1")
os.environ.setdefault("SHOPEE_PARTNER_KEY", "test_key")
os.environ.setdefault("SHOPEE_SHOP_ID", "123")


@pytest.fixture(scope="session")
def event_loop() -> Iterator[asyncio.AbstractEventLoop]:
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture
async def redis() -> AsyncIterator[FakeRedis]:
    r = FakeRedis(decode_responses=True)
    yield r
    await r.flushall()
    await r.aclose()


@pytest_asyncio.fixture(autouse=True)
async def cleanup_redis_singleton() -> AsyncIterator[None]:
    """Cleanup Redis singleton after each test to prevent connection leaks."""
    yield
    import src.core.redis

    if src.core.redis._redis is not None:
        with contextlib.suppress(Exception):
            await src.core.redis._redis.aclose()
        src.core.redis._redis = None


@pytest.fixture
def shopee_order_payload() -> dict:
    return {
        "code": 3,
        "shop_id": 123456,
        "timestamp": 1700000000,
        "data": {"ordersn": "230101ABCDEF01", "status": "READY_TO_SHIP", "update_time": 1700000000},
    }


@pytest.fixture
def shopee_order_detail_raw() -> dict:
    return {
        "order_sn": "230101ABCDEF01",
        "order_status": "READY_TO_SHIP",
        "payment_method": "Cash on Delivery",
        "buyer_user_id": 99,
        "buyer_username": "test_buyer",
        "create_time": 1700000000,
        "update_time": 1700000001,
        "pay_time": None,
        "total_amount": 200000,
        "actual_shipping_fee": 25000,
        "voucher_from_shopee": 0,
        "voucher_from_seller": 0,
        "recipient_address": {
            "name": "Nguyen Van A",
            "phone": "0901234567",
            "full_address": "123 Le Loi",
            "district": "Quan 1",
            "state": "TP HCM",
            "town": "Phuong Ben Nghe",
        },
        "item_list": [
            {
                "item_id": 1,
                "model_id": 0,
                "item_sku": "SKU-001",
                "item_name": "Test Product",
                "model_name": "Red-L",
                "model_quantity_purchased": 2,
                "model_original_price": 100000,
                "model_discounted_price": 87500,
            }
        ],
        "package_list": [{"tracking_number": "SPXVN001", "shipping_carrier": "SPX"}],
    }
