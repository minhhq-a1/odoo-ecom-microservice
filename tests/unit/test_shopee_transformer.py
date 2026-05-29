"""src.transformers.shopee unit tests."""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.schemas.unified import OrderStatus, Platform
from src.transformers.shopee import SHOPEE_STATUS_MAP, ShopeeTransformer


@pytest.mark.parametrize(
    ("raw_status", "expected"),
    [
        ("UNPAID", OrderStatus.PENDING),
        ("READY_TO_SHIP", OrderStatus.CONFIRMED),
        ("PROCESSED", OrderStatus.PROCESSING),
        ("SHIPPED", OrderStatus.SHIPPED),
        ("COMPLETED", OrderStatus.DELIVERED),
        ("CANCELLED", OrderStatus.CANCELLED),
        ("TO_RETURN", OrderStatus.RETURN_REQUESTED),
    ],
)
def test_status_mapping(raw_status: str, expected: OrderStatus) -> None:
    assert SHOPEE_STATUS_MAP[raw_status] == expected


def test_transform_basic(shopee_order_detail_raw: dict) -> None:
    order = ShopeeTransformer().transform(shopee_order_detail_raw)
    assert order.platform is Platform.SHOPEE
    assert order.platform_order_id == "230101ABCDEF01"
    assert order.status is OrderStatus.CONFIRMED
    assert len(order.items) == 1
    assert order.items[0].sku == "SKU-001"
    assert order.items[0].quantity == 2
    assert order.items[0].discounted_price == Decimal("87500")
    assert order.total_amount == Decimal("200000")
    assert order.shipping_address.phone == "0901234567"


def test_transform_phone_normalize() -> None:
    raw = {
        "order_sn": "X",
        "order_status": "UNPAID",
        "recipient_address": {
            "phone": "+84-901-234-567",
            "name": "A",
            "full_address": "x",
            "district": "x",
            "state": "x",
        },
        "item_list": [],
    }
    order = ShopeeTransformer().transform(raw)
    assert order.shipping_address.phone == "0901234567"
