"""Platform-agnostic unified schemas."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any


class Platform(StrEnum):
    SHOPEE = "shopee"
    LAZADA = "lazada"
    TIKTOK = "tiktok"


class OrderStatus(StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    PROCESSING = "processing"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    RETURN_REQUESTED = "return_requested"
    RETURNED = "returned"


class PaymentMethod(StrEnum):
    COD = "cod"
    ONLINE = "online"
    INSTALLMENT = "installment"
    UNKNOWN = "unknown"


STATUS_ORDER: dict[OrderStatus, int] = {
    OrderStatus.PENDING: 0,
    OrderStatus.CONFIRMED: 1,
    OrderStatus.PROCESSING: 2,
    OrderStatus.SHIPPED: 3,
    OrderStatus.DELIVERED: 4,
    OrderStatus.RETURN_REQUESTED: 5,
    OrderStatus.RETURNED: 6,
    OrderStatus.CANCELLED: 99,
}


def should_update_status(current: OrderStatus, new: OrderStatus) -> bool:
    if new is OrderStatus.CANCELLED:
        return current in (OrderStatus.PENDING, OrderStatus.CONFIRMED)
    return STATUS_ORDER[new] > STATUS_ORDER[current]


@dataclass
class UnifiedAddress:
    full_name: str
    phone: str
    address_line: str
    district: str
    province: str
    ward: str | None = None
    country: str = "VN"
    postal_code: str | None = None


@dataclass
class UnifiedOrderItem:
    platform_item_id: str
    sku: str
    product_name: str
    quantity: int
    unit_price: Decimal
    discounted_price: Decimal
    discount_amount: Decimal
    platform_variant_id: str | None = None
    variant_name: str | None = None
    image_url: str | None = None


@dataclass
class UnifiedLogistics:
    shipping_fee: Decimal
    tracking_number: str | None = None
    carrier_name: str | None = None
    carrier_code: str | None = None
    estimated_delivery: datetime | None = None
    shipping_fee_discount: Decimal = Decimal("0")


@dataclass
class UnifiedOrder:
    platform: Platform
    platform_order_id: str
    status: OrderStatus
    payment_method: PaymentMethod
    buyer_platform_id: str
    buyer_username: str
    shipping_address: UnifiedAddress
    items: list[UnifiedOrderItem]
    subtotal: Decimal
    shipping_fee: Decimal
    platform_discount: Decimal
    seller_discount: Decimal
    total_amount: Decimal
    platform_order_sn: str | None = None
    logistics: UnifiedLogistics | None = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    paid_at: datetime | None = None
    raw_payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class UnifiedProduct:
    platform: Platform
    platform_product_id: str
    platform_sku_id: str
    sku: str
    name: str
    stock_quantity: int
    price: Decimal
    is_active: bool = True


@dataclass
class StockUpdateRequest:
    platform: Platform
    sku: str
    quantity: int
    reason: str = "sync"
