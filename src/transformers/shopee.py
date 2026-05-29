"""Shopee → UnifiedOrder transformer."""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from src.core.utils import normalize_vn_phone, safe_int
from src.schemas.unified import (
    OrderStatus,
    PaymentMethod,
    Platform,
    UnifiedAddress,
    UnifiedLogistics,
    UnifiedOrder,
    UnifiedOrderItem,
)

SHOPEE_STATUS_MAP: dict[str, OrderStatus] = {
    "UNPAID": OrderStatus.PENDING,
    "READY_TO_SHIP": OrderStatus.CONFIRMED,
    "PROCESSED": OrderStatus.PROCESSING,
    "RETRY_SHIP": OrderStatus.PROCESSING,
    "SHIPPED": OrderStatus.SHIPPED,
    "TO_CONFIRM_RECEIVE": OrderStatus.SHIPPED,
    "COMPLETED": OrderStatus.DELIVERED,
    "IN_CANCEL": OrderStatus.CANCELLED,
    "CANCELLED": OrderStatus.CANCELLED,
    "TO_RETURN": OrderStatus.RETURN_REQUESTED,
    "INVOICE_PENDING": OrderStatus.CONFIRMED,
}

SHOPEE_PAYMENT_MAP: dict[str, PaymentMethod] = {
    "COD": PaymentMethod.COD,
    "Cash on Delivery": PaymentMethod.COD,
}


def _ts_to_dt(ts: int | None) -> datetime | None:
    if not ts:
        return None
    return datetime.fromtimestamp(ts, tz=UTC)


def _D(x: object) -> Decimal:
    if x is None or x == "":
        return Decimal("0")
    return Decimal(str(x))


class ShopeeTransformer:
    def transform(self, raw: dict[str, Any]) -> UnifiedOrder:
        recipient = raw.get("recipient_address") or {}
        address = UnifiedAddress(
            full_name=recipient.get("name", ""),
            phone=normalize_vn_phone(recipient.get("phone", "")),
            address_line=recipient.get("full_address", ""),
            ward=recipient.get("town"),
            district=recipient.get("district", ""),
            province=recipient.get("state", ""),
            country="VN",
            postal_code=recipient.get("zipcode"),
        )

        items: list[UnifiedOrderItem] = []
        for it in raw.get("item_list", []):
            unit = _D(it.get("model_original_price") or it.get("model_discounted_price"))
            disc = _D(it.get("model_discounted_price") or it.get("model_original_price"))
            items.append(UnifiedOrderItem(
                platform_item_id=str(it.get("item_id", "")),
                platform_variant_id=str(it.get("model_id") or "") or None,
                sku=it.get("model_sku") or it.get("item_sku") or "",
                product_name=it.get("item_name", ""),
                variant_name=it.get("model_name") or None,
                quantity=safe_int(it.get("model_quantity_purchased")),
                unit_price=unit,
                discounted_price=disc,
                discount_amount=unit - disc,
                image_url=it.get("image_info", {}).get("image_url"),
            ))

        logistics = None
        ship = raw.get("package_list") or []
        if ship:
            pkg = ship[0]
            logistics = UnifiedLogistics(
                shipping_fee=_D(raw.get("actual_shipping_fee") or raw.get("estimated_shipping_fee")),
                tracking_number=pkg.get("tracking_number"),
                carrier_name=pkg.get("shipping_carrier"),
            )

        return UnifiedOrder(
            platform=Platform.SHOPEE,
            platform_order_id=str(raw.get("order_sn", "")),
            platform_order_sn=str(raw.get("order_sn", "")),
            status=SHOPEE_STATUS_MAP.get(raw.get("order_status", ""), OrderStatus.PENDING),
            payment_method=SHOPEE_PAYMENT_MAP.get(
                raw.get("payment_method", ""), PaymentMethod.ONLINE,
            ),
            buyer_platform_id=str(raw.get("buyer_user_id", "")),
            buyer_username=raw.get("buyer_username", ""),
            shipping_address=address,
            items=items,
            subtotal=sum((it.discounted_price * it.quantity for it in items), Decimal("0")),
            shipping_fee=_D(raw.get("actual_shipping_fee") or raw.get("estimated_shipping_fee")),
            platform_discount=_D(raw.get("voucher_from_shopee")),
            seller_discount=_D(raw.get("voucher_from_seller")),
            total_amount=_D(raw.get("total_amount")),
            logistics=logistics,
            created_at=_ts_to_dt(raw.get("create_time")) or datetime.now(UTC),
            updated_at=_ts_to_dt(raw.get("update_time")) or datetime.now(UTC),
            paid_at=_ts_to_dt(raw.get("pay_time")),
            raw_payload=raw,
        )
