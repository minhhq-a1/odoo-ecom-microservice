# DATA MODELS & SCHEMAS
## Unified Schemas, Database Models, API Contracts

---

## 1. Unified Order Schema (Platform-agnostic)

```python
# src/schemas/unified.py

from enum import Enum
from dataclasses import dataclass, field
from typing import List, Optional
from datetime import datetime
from decimal import Decimal

# ─── Enums ───────────────────────────────────────────────────────────────────

class Platform(str, Enum):
    SHOPEE  = "shopee"
    LAZADA  = "lazada"
    TIKTOK  = "tiktok"

class OrderStatus(str, Enum):
    PENDING           = "pending"           # Chờ thanh toán
    CONFIRMED         = "confirmed"         # Đã thanh toán / sẵn sàng xử lý
    PROCESSING        = "processing"        # Đang đóng gói
    SHIPPED           = "shipped"           # Đã giao vận chuyển
    DELIVERED         = "delivered"         # Đã giao khách
    CANCELLED         = "cancelled"         # Đã hủy
    RETURN_REQUESTED  = "return_requested"  # Yêu cầu trả hàng
    RETURNED          = "returned"          # Đã trả hàng

class PaymentMethod(str, Enum):
    COD           = "cod"
    ONLINE        = "online"
    INSTALLMENT   = "installment"
    UNKNOWN       = "unknown"

# ─── Sub-schemas ─────────────────────────────────────────────────────────────

@dataclass
class UnifiedAddress:
    full_name:    str
    phone:        str
    address_line: str
    ward:         Optional[str]
    district:     str
    province:     str
    country:      str = "VN"
    postal_code:  Optional[str] = None

@dataclass
class UnifiedOrderItem:
    platform_item_id:    str
    platform_variant_id: Optional[str]
    sku:                 str             # Odoo internal_reference
    product_name:        str
    variant_name:        Optional[str]  # Màu sắc, size...
    quantity:            int
    unit_price:          Decimal         # Giá gốc
    discounted_price:    Decimal         # Giá sau giảm
    discount_amount:     Decimal
    image_url:           Optional[str] = None

@dataclass
class UnifiedLogistics:
    tracking_number:       Optional[str]
    carrier_name:          Optional[str]
    carrier_code:          Optional[str]  # GHTK, GHN, J&T...
    estimated_delivery:    Optional[datetime]
    shipping_fee:          Decimal
    shipping_fee_discount: Decimal = Decimal("0")

# ─── Main Schema ─────────────────────────────────────────────────────────────

@dataclass
class UnifiedOrder:
    # Identifiers
    platform:           Platform
    platform_order_id:  str
    platform_order_sn:  Optional[str]   # Shopee dùng order_sn

    # Status
    status:             OrderStatus
    payment_method:     PaymentMethod

    # Buyer
    buyer_platform_id:  str
    buyer_username:     str
    shipping_address:   UnifiedAddress

    # Items
    items:              List[UnifiedOrderItem]

    # Financials
    subtotal:           Decimal
    shipping_fee:       Decimal
    platform_discount:  Decimal         # Shopee voucher
    seller_discount:    Decimal         # Seller voucher
    total_amount:       Decimal         # Số tiền thực tế shop nhận

    # Logistics
    logistics:          Optional[UnifiedLogistics] = None

    # Timestamps
    created_at:         datetime = field(default_factory=datetime.utcnow)
    updated_at:         datetime = field(default_factory=datetime.utcnow)
    paid_at:            Optional[datetime] = None

    # Raw data (luôn giữ lại để debug)
    raw_payload:        dict = field(default_factory=dict)
```

---

## 2. Unified Product / Stock Schema

```python
@dataclass
class UnifiedProduct:
    platform:           Platform
    platform_product_id: str
    platform_sku_id:    str
    sku:                str             # Odoo internal_reference
    name:               str
    stock_quantity:     int
    price:              Decimal
    is_active:          bool = True

@dataclass
class StockUpdateRequest:
    platform:   Platform
    sku:        str
    quantity:   int                     # Số lượng muốn set trên sàn
    reason:     str = "sync"            # Lý do update (audit)
```

---

## 3. Database Models (SQLAlchemy)

```python
# src/models/

# ── order_mapping ──────────────────────────────────────────────────────────
class OrderMapping(Base):
    __tablename__ = "order_mapping"

    id                  = Column(Integer, primary_key=True)
    platform            = Column(String(20), nullable=False)
    platform_order_id   = Column(String(100), nullable=False)
    platform_order_sn   = Column(String(100))
    odoo_order_id       = Column(Integer)
    odoo_order_name     = Column(String(50))
    status              = Column(String(20), default="pending")
    # pending | syncing | success | failed | skipped
    retry_count         = Column(Integer, default=0)
    last_error          = Column(Text)
    synced_at           = Column(DateTime)
    created_at          = Column(DateTime, default=func.now())
    updated_at          = Column(DateTime, onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("platform", "platform_order_id"),
    )

# ── webhook_outbox ─────────────────────────────────────────────────────────
class WebhookOutbox(Base):
    __tablename__ = "webhook_outbox"

    id                  = Column(BigInteger, primary_key=True)
    platform            = Column(String(20), nullable=False)
    event_type          = Column(String(50), nullable=False)  # order|logistics|stock
    event_code          = Column(Integer)
    platform_order_id   = Column(String(100))
    payload             = Column(JSONB, nullable=False)
    signature           = Column(String(255))
    status              = Column(String(20), default="pending")
    # pending | processing | published | failed | dead_letter
    retry_count         = Column(Integer, default=0)
    max_retries         = Column(Integer, default=5)
    last_error          = Column(Text)
    process_after       = Column(DateTime(timezone=True), server_default=func.now())
    published_at        = Column(DateTime(timezone=True))
    created_at          = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("idx_outbox_pending", "status", "process_after",
              postgresql_where=text("status IN ('pending','failed')")),
        Index("idx_outbox_platform_order", "platform", "platform_order_id"),
    )

# ── order_sync_log ─────────────────────────────────────────────────────────
class OrderSyncLog(Base):
    __tablename__ = "order_sync_log"

    id              = Column(Integer, primary_key=True)
    order_mapping_id= Column(Integer, ForeignKey("order_mapping.id"))
    action          = Column(String(50))  # create/update/cancel/ship
    status          = Column(String(20))  # success/failed
    request_payload = Column(JSONB)
    response_payload= Column(JSONB)
    error_message   = Column(Text)
    duration_ms     = Column(Integer)
    created_at      = Column(DateTime, default=func.now())

# ── product_mapping ────────────────────────────────────────────────────────
class ProductMapping(Base):
    __tablename__ = "product_mapping"

    id                   = Column(Integer, primary_key=True)
    platform             = Column(String(20), nullable=False)
    platform_product_id  = Column(String(100), nullable=False)
    platform_sku_id      = Column(String(100))
    odoo_product_id      = Column(Integer, nullable=False)
    odoo_sku             = Column(String(100), nullable=False)
    mapping_type         = Column(String(20), default="simple")
    # simple | bundle — nếu bundle, xem bảng product_bundle_components
    is_active            = Column(Boolean, default=True)
    created_at           = Column(DateTime, default=func.now())

    __table_args__ = (
        UniqueConstraint("platform", "platform_sku_id"),
    )

# ── product_bundle_components ──────────────────────────────────────────────
class ProductBundleComponent(Base):
    """1 SKU sàn = nhiều SKU Odoo (combo/bundle)"""
    __tablename__ = "product_bundle_components"

    id              = Column(Integer, primary_key=True)
    mapping_id      = Column(Integer, ForeignKey("product_mapping.id"), nullable=False)
    odoo_sku        = Column(String(100), nullable=False)
    quantity        = Column(Integer, nullable=False, default=1)

# ── stock_allocation_config ────────────────────────────────────────────────
class StockAllocationConfig(Base):
    __tablename__ = "stock_allocation_config"

    id              = Column(Integer, primary_key=True)
    odoo_sku        = Column(String(100), nullable=False)
    platform        = Column(String(20), nullable=False)
    allocation_pct  = Column(Numeric(5, 2), nullable=False)
    buffer_pct      = Column(Numeric(5, 2), default=10.00)
    is_active       = Column(Boolean, default=True)

    __table_args__ = (
        UniqueConstraint("odoo_sku", "platform"),
    )

# ── platform_config ────────────────────────────────────────────────────────
class PlatformConfig(Base):
    __tablename__ = "platform_config"

    id              = Column(Integer, primary_key=True)
    platform        = Column(String(20), unique=True, nullable=False)
    shop_id         = Column(String(100))
    credentials     = Column(JSONB)         # Encrypted
    is_active       = Column(Boolean, default=True)
    dry_run         = Column(Boolean, default=False)  # ← Dry-run per platform
    price_master    = Column(String(20), default="platform")
    # "platform" = giá từ sàn (default), "odoo" = giá từ Odoo
    extra_config    = Column(JSONB)
    created_at      = Column(DateTime, default=func.now())
    updated_at      = Column(DateTime, onupdate=func.now())

# ── reconciliation_log ─────────────────────────────────────────────────────
class ReconciliationLog(Base):
    """Kết quả nightly job so sánh sàn ↔ Odoo"""
    __tablename__ = "reconciliation_log"

    id              = Column(Integer, primary_key=True)
    platform        = Column(String(20), nullable=False)
    run_date        = Column(Date, nullable=False)
    orders_checked  = Column(Integer, default=0)
    orders_matched  = Column(Integer, default=0)
    orders_missing  = Column(Integer, default=0)  # Có trên sàn, không có Odoo
    orders_extra    = Column(Integer, default=0)   # Có trên Odoo, không có sàn
    auto_fixed      = Column(Integer, default=0)   # Tự động sync thành công
    needs_review    = Column(Integer, default=0)   # Cần xem xét thủ công
    details         = Column(JSONB)                # Chi tiết từng order bị miss
    created_at      = Column(DateTime, default=func.now())

# ── price_sync_log ─────────────────────────────────────────────────────────
class PriceSyncLog(Base):
    """Lịch sử sync giá từ Odoo → sàn"""
    __tablename__ = "price_sync_log"

    id              = Column(Integer, primary_key=True)
    platform        = Column(String(20), nullable=False)
    odoo_sku        = Column(String(100), nullable=False)
    old_price       = Column(Numeric(15, 2))
    new_price       = Column(Numeric(15, 2))
    status          = Column(String(20))   # success | failed | skipped
    error_message   = Column(Text)
    created_at      = Column(DateTime, default=func.now())
```

---

## 4. API Request/Response Schemas (Pydantic v2)

```python
# src/api/schemas.py

from pydantic import BaseModel

class WebhookResponse(BaseModel):
    status:  str = "ok"
    message: str = "received"

class SyncOrderRequest(BaseModel):
    platform:         str
    platform_order_id: str
    force:            bool = False  # Force re-sync dù đã success

class SyncOrderResponse(BaseModel):
    success:          bool
    odoo_order_id:    Optional[int]
    odoo_order_name:  Optional[str]
    message:          str

class StockSyncRequest(BaseModel):
    sku:              str
    platforms:        List[str] = []  # Empty = sync tất cả

class HealthResponse(BaseModel):
    status:           str            # healthy/degraded/unhealthy
    redis:            bool
    postgres:         bool
    odoo:             bool
    celery_workers:   int
```

---

## 5. Odoo 18 Field Mapping

```python
# Mapping giữa UnifiedOrder → Odoo sale.order

ODOO_ORDER_FIELDS = {
    "partner_id":       "from buyer → res.partner",
    "origin":           "f'{platform}_{platform_order_id}'",
    "x_platform":       "platform name (custom field)",
    "x_platform_order_id": "platform_order_id (custom field)",
    "x_platform_order_sn": "platform_order_sn (custom field)",
    "note":             "buyer notes if any",
    "order_line":       "[(0, 0, line_dict), ...]",
}

ODOO_ORDER_LINE_FIELDS = {
    "product_id":       "odoo_product_id từ product_mapping",
    "product_uom_qty":  "quantity",
    "price_unit":       "discounted_price",
    "name":             "product_name + variant_name",
}

# Custom fields cần tạo trên Odoo 18
ODOO_CUSTOM_FIELDS = [
    ("sale.order", "x_platform",           "char",     "Platform"),
    ("sale.order", "x_platform_order_id",  "char",     "Platform Order ID"),
    ("sale.order", "x_platform_order_sn",  "char",     "Platform Order SN"),
    ("sale.order", "x_sync_status",        "selection","Sync Status"),
]
```
