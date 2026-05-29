# BUSINESS RULES & EDGE CASES
## Quy tắc nghiệp vụ, xử lý ngoại lệ và các tình huống đặc biệt

---

## 1. Idempotency — Không tạo duplicate

### Rule
Mọi order sync operation PHẢI kiểm tra tồn tại trước khi tạo mới.

### Implementation
```python
# Kiểm tra theo thứ tự:
# 1. order_mapping table (nhanh nhất)
# 2. Odoo x_platform_order_id field (fallback)

def is_duplicate(platform: str, platform_order_id: str) -> bool:
    # Check middleware DB trước
    mapping = db.query(OrderMapping).filter_by(
        platform=platform,
        platform_order_id=platform_order_id,
        status="success"
    ).first()
    if mapping:
        return True
    
    # Double-check Odoo (phòng trường hợp DB middleware bị reset)
    return odoo.check_order_exists(platform, platform_order_id) is not None
```

### Trường hợp đặc biệt
- Webhook gửi 2 lần cùng event → chỉ xử lý 1 lần
- Polling fallback trùng với webhook → skip
- Retry sau fail → KHÔNG tạo mới nếu Odoo đã có

---

## 2. Order Status Machine

### Luồng trạng thái hợp lệ

```
PENDING → CONFIRMED → PROCESSING → SHIPPED → DELIVERED
                    ↘ CANCELLED
DELIVERED → RETURN_REQUESTED → RETURNED
```

### Rule: Chỉ update Odoo khi trạng thái TIẾN (forward)

```python
STATUS_ORDER = {
    OrderStatus.PENDING:          0,
    OrderStatus.CONFIRMED:        1,
    OrderStatus.PROCESSING:       2,
    OrderStatus.SHIPPED:          3,
    OrderStatus.DELIVERED:        4,
    OrderStatus.RETURN_REQUESTED: 5,
    OrderStatus.RETURNED:         6,
    OrderStatus.CANCELLED:        99,
}

def should_update_status(current: OrderStatus, new: OrderStatus) -> bool:
    if new == OrderStatus.CANCELLED:
        # Chỉ cancel được từ PENDING hoặc CONFIRMED
        return current in (OrderStatus.PENDING, OrderStatus.CONFIRMED)
    return STATUS_ORDER[new] > STATUS_ORDER[current]
```

---

## 3. Stock Sync Rules

### Rule 1: Buffer bắt buộc
- Luôn giữ lại ít nhất 10% tồn kho, không đẩy 100% lên sàn
- Configurable per SKU trong stock_allocation_config

### Rule 2: Flash Sale Lock
- Khi Shopee báo `error_item_is_on_flash_sale` → KHÔNG update stock
- Log warning, tiếp tục với các sàn khác

### Rule 3: Stock Sync Priority
```
Trigger Priority (cao → thấp):
1. Sau khi delivery xác nhận (Odoo) → sync ngay lập tức
2. Sau khi order confirmed (sàn)     → sync trong 1 phút
3. Scheduled job mỗi 15 phút         → safety net
```

### Rule 4: Negative stock prevention
```python
def calculate_platform_stock(odoo_stock: int, sku: str, platform: str) -> int:
    config  = get_allocation_config(sku, platform)
    buffer  = int(odoo_stock * config.buffer_pct / 100)
    available = max(0, odoo_stock - buffer)
    allocated = int(available * config.allocation_pct / 100)
    return max(0, allocated)  # KHÔNG BAO GIỜ âm
```

---

## 4. Product Mapping Rules

### Rule: SKU là key mapping
- Odoo `product.product.default_code` (internal_reference) = SKU
- SKU phải duy nhất trên Odoo
- Nếu không tìm thấy SKU → log error + dead letter, KHÔNG tạo order

### Xử lý khi không tìm thấy SKU
```python
def get_product_mapping(sku: str) -> Optional[int]:
    # 1. Check cache Redis (TTL 1h)
    cached = redis.get(f"product:sku:{sku}")
    if cached:
        return int(cached)
    
    # 2. Check mapping table
    mapping = db.query(ProductMapping).filter_by(odoo_sku=sku).first()
    if mapping:
        redis.setex(f"product:sku:{sku}", 3600, mapping.odoo_product_id)
        return mapping.odoo_product_id
    
    # 3. Tìm trực tiếp trên Odoo
    odoo_id = odoo_client.get_product_by_sku(sku)
    if odoo_id:
        # Tự động tạo mapping
        create_product_mapping(sku=sku, odoo_product_id=odoo_id)
        return odoo_id
    
    # 4. Không tìm thấy → alert
    alert_missing_sku(sku)
    return None
```

---

## 5. Customer/Partner Rules

### Rule: Tìm theo phone, không tạo duplicate customer

```python
# Normalize phone về dạng 0xxxxxxxxx (10 số, bắt đầu 0)
def normalize_vn_phone(phone: str) -> str:
    phone = re.sub(r'\D', '', phone)           # Bỏ ký tự không phải số
    if phone.startswith('84'):
        phone = '0' + phone[2:]
    elif phone.startswith('+84'):
        phone = '0' + phone[3:]
    return phone

# Tìm partner theo phone trước
# Nếu trùng tên + phone → dùng lại
# Nếu khác tên nhưng cùng phone → dùng lại (buyer đổi tên)
# Nếu không tìm thấy → tạo mới
```

---

## 6. Xử lý Flash Sale / Traffic Spike

### Vấn đề
Flash sale có thể tạo 500+ đơn trong vài phút → queue có thể tăng đột biến.

### Giải pháp
```python
# 1. Queue tách HIGH priority cho flash sale orders
# Phát hiện: order có discount cao bất thường hoặc sàn gắn tag flash_sale

# 2. Auto-scale workers (nếu dùng K8s)
# Trigger scale khi queue depth > 200

# 3. Circuit breaker cho Odoo
# Nếu Odoo response time > 5s hoặc error rate > 20%
# → Tạm dừng nhận thêm task, đợi Odoo hồi phục

# 4. Rate limit inbound webhooks
# Max 100 webhooks/giây từ cùng một sàn
```

---

## 7. Token Management Rules

### Shopee Token Lifecycle
```
access_token:   4 giờ
refresh_token:  30 ngày

Refresh strategy:
- Celery Beat job chạy mỗi 3 giờ → refresh access_token
- Nếu refresh_token sắp hết (< 7 ngày) → alert Slack
- Nếu refresh_token hết hạn → manual re-auth required → alert critical

Lưu trữ:
- Redis: shopee:token:{shop_id}:access   TTL=14000 (3.9h)
- Redis: shopee:token:{shop_id}:refresh  TTL=2500000 (28 ngày)
- DB:    platform_config.credentials (backup)
```

---

## 8. Cancellation Rules

### Khi sàn hủy đơn
```python
def handle_order_cancellation(platform_order_id: str, platform: str):
    mapping = get_order_mapping(platform, platform_order_id)
    
    if not mapping or mapping.status != "success":
        # Chưa sync sang Odoo → chỉ cập nhật mapping
        update_mapping_status(mapping, "cancelled")
        return
    
    odoo_order = odoo.get_sale_order(mapping.odoo_order_id)
    
    if odoo_order["state"] == "draft":
        odoo.cancel_order(mapping.odoo_order_id)
    elif odoo_order["state"] == "sale":
        # Đã confirm → cần check xem đã có picking chưa
        picking = odoo.get_picking_for_order(mapping.odoo_order_id)
        if picking and picking["state"] == "done":
            # Đã giao hàng → KHÔNG cancel, tạo return request thủ công
            alert_manual_action_required(mapping, "Cannot cancel: delivery done")
        else:
            odoo.cancel_order(mapping.odoo_order_id)
```

---

## 9. Retry & Dead Letter Rules

### Retry Policy per Error Type
```python
RETRY_POLICY = {
    OdooConnectionError:    {"max_retries": 10, "countdown": 60},
    ShopeeRateLimitError:   {"max_retries": 5,  "countdown": "dynamic"},
    OdooValidationError:    {"max_retries": 0,  "action": "dead_letter"},
    ProductNotFoundError:   {"max_retries": 3,  "countdown": 300},
    DuplicateOrderError:    {"max_retries": 0,  "action": "skip"},
}
```

### Dead Letter Actions
- Lưu vào `order_sync_log` với status `dead_letter`
- Gửi alert Slack với đầy đủ thông tin
- Cho phép manual re-sync qua Admin API: `POST /admin/orders/{id}/retry`

---

## 10. Monitoring Thresholds

```python
ALERT_THRESHOLDS = {
    "dead_letter_count":     {"warning": 1,   "critical": 5},    # per 5 phút
    "queue_depth_orders":    {"warning": 500, "critical": 2000},
    "sync_lag_minutes":      {"warning": 5,   "critical": 15},
    "odoo_response_time_ms": {"warning": 3000,"critical": 8000},
    "token_expire_hours":    {"warning": 24,  "critical": 1},     # refresh_token
    "worker_count":          {"warning": 1,   "critical": 0},     # min workers
    "error_rate_percent":    {"warning": 5,   "critical": 15},    # per 10 phút
}
```

---

## 11. Dry-run Mode

### Mục đích
Nhận và transform dữ liệu thật, nhưng KHÔNG tạo order/update tồn kho trên Odoo hoặc sàn. Dùng khi:
- Go-live lần đầu để kiểm tra mapping SKU, customer data
- Deploy major update để verify logic mới

### Config
```python
# .env
MIDDLEWARE_DRY_RUN=true   # Global dry-run

# Hoặc per-platform trong platform_config.dry_run = True
```

### Behavior khi dry-run=True
```python
def create_sale_order(self, order: UnifiedOrder) -> tuple[int, str]:
    if settings.MIDDLEWARE_DRY_RUN or self._is_platform_dry_run(order.platform):
        logger.info("dry_run_order_skipped",
                    platform=order.platform,
                    platform_order_id=order.platform_order_id,
                    items=[i.sku for i in order.items])
        # Simulate thành công nhưng không ghi vào Odoo
        return -1, "DRY-RUN"
    # ... logic thật
```

### Checklist trước khi tắt dry-run
```
✅ Tất cả SKU trong đơn test đều resolve thành công
✅ Customer phone/address format hợp lệ
✅ Không có ValidationError nào trong log
✅ Stock calculation ra kết quả đúng
✅ Load test pass trên staging
```

---

## 12. Reconciliation Job (Nightly)

### Mục đích
Lớp bảo vệ cuối cùng. Mỗi đêm lúc 2:00 AM, so sánh toàn bộ đơn hàng của ngày hôm trước giữa sàn và Odoo, phát hiện các đơn bị miss mà webhook + polling đều bỏ sót.

### Logic

```python
class ReconciliationService:

    async def run(self, platform: str, date: date) -> ReconciliationResult:
        # 1. Lấy danh sách đơn từ sàn (qua API, không phải webhook)
        platform_orders = await self.connector.get_orders_by_date(date)

        # 2. Lấy danh sách order_mapping đã sync thành công trong ngày
        synced_ids = await self.get_synced_order_ids(platform, date)

        # 3. Tìm các đơn có trên sàn nhưng KHÔNG có trong Odoo
        missing = [o for o in platform_orders
                   if o.platform_order_id not in synced_ids]

        # 4. Tự động sync các đơn bị miss
        auto_fixed = 0
        needs_review = []
        for order in missing:
            # Chỉ auto-sync các status an toàn
            if order.status in (OrderStatus.CONFIRMED, OrderStatus.PROCESSING):
                success = await self.sync_missing_order(order)
                if success:
                    auto_fixed += 1
                else:
                    needs_review.append(order)
            else:
                needs_review.append(order)

        # 5. Lưu kết quả và alert nếu có needs_review
        await self.save_result(platform, date, missing, auto_fixed, needs_review)

        if needs_review:
            await AlertService.send_reconciliation_alert(needs_review)

        return ReconciliationResult(
            orders_checked=len(platform_orders),
            orders_missing=len(missing),
            auto_fixed=auto_fixed,
            needs_review=len(needs_review),
        )
```

### Schedule
```python
# Celery Beat
"nightly-reconciliation": {
    "task":     "reconciliation.run_all_platforms",
    "schedule": crontab(hour=2, minute=0),  # 2:00 AM mỗi ngày
}
```

### Xem kết quả
- Admin UI > tab mới "Reconciliation" → xem lịch sử các lần chạy
- Slack alert khi có `needs_review > 0`

---

## 13. Price Sync (Odoo → Sàn)

### Quyết định quan trọng: Price Master ở đâu?

```
Option A: Platform is master (default)
  → Giá đặt trực tiếp trên Shopee/Lazada
  → Odoo chỉ ghi nhận giá thực tế từ đơn hàng
  → Đơn giản hơn, phù hợp hầu hết shop

Option B: Odoo is master
  → Giá đặt trên Odoo (pricelist)
  → Middleware sync giá lên sàn mỗi 6h
  → Phức tạp hơn, phù hợp khi có nhiều kênh bán
```

Config trong `platform_config.price_master`:
- `"platform"` → không cần làm gì thêm
- `"odoo"` → bật price sync job

### Price Sync khi Odoo là master

```python
# Trigger: Celery Beat mỗi 6h
# Hoặc: Odoo webhook khi product.template.list_price thay đổi

async def sync_price_to_platform(sku: str, platform: str):
    config = get_platform_config(platform)
    if config.price_master != "odoo":
        return  # Skip nếu platform là master

    # Lấy giá từ Odoo pricelist
    odoo_price = odoo_client.get_product_price(sku, pricelist="VND")

    # Lấy giá hiện tại trên sàn để kiểm tra có cần update không
    current_price = await connector.get_product_price(sku)
    if current_price == odoo_price:
        return  # Không thay đổi → skip

    # Update lên sàn
    await connector.update_product_price(sku, odoo_price)

    # Log
    await save_price_sync_log(sku, platform, current_price, odoo_price)
```

### ⚠️ Lưu ý
- Khi flash sale đang chạy: KHÔNG update giá (Shopee lock)
- Giá phải > 0 và hợp lệ với range của từng sàn
- Log tất cả thay đổi giá vào `price_sync_log`

---

## 14. Admin API Rate Limiting

### Vấn đề
Endpoint `/admin/orders/retry-all-failed` nếu không giới hạn có thể bị bấm liên tục → flood queue.

### Implementation

```python
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)

@router.post("/orders/retry-all-failed")
@limiter.limit("1/minute")  # Tối đa 1 lần/phút per IP
async def retry_all_failed(request: Request, ...):
    ...

@router.post("/stock/sync-all")
@limiter.limit("1/5minutes")  # Stock sync toàn bộ: 1 lần/5 phút
async def sync_all_stock(request: Request, ...):
    ...
```
