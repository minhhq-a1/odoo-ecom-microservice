# GAP PATCHES — v2.1
## Bổ sung models, business rules, performance optimizations còn thiếu

> Doc này vá các gap phát hiện trong code review v2.0. Khi conflict, doc này thắng.

---

## 1. WebhookEventLog Model (đã mention nhưng thiếu schema)

```python
# src/models/webhook_event_log.py
class WebhookEventLog(Base):
    """
    Raw webhook events — audit trail.
    KHÁC outbox: log mọi sự kiện nhận được (kể cả signature invalid, duplicate).
    Outbox chỉ chứa events đã verify + đang chờ publish.
    """
    __tablename__ = "webhook_event_log"

    id              = Column(BigInteger, primary_key=True)
    platform        = Column(String(20), nullable=False)
    received_at     = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    source_ip       = Column(INET)
    signature       = Column(String(255))
    signature_valid = Column(Boolean, nullable=False)
    headers         = Column(JSONB)
    body_raw        = Column(BYTEA)         # Original bytes để verify lại
    body_size       = Column(Integer)
    event_code      = Column(Integer)
    platform_order_id = Column(String(100), index=True)
    outbox_id       = Column(BigInteger, ForeignKey("webhook_outbox.id"), nullable=True)
    duplicate_of    = Column(BigInteger, ForeignKey("webhook_event_log.id"), nullable=True)
    parse_error     = Column(Text)

    __table_args__ = (
        Index("idx_webhook_event_received", "received_at"),
        Index("idx_webhook_event_platform_order", "platform", "platform_order_id"),
    )

# Retention: 30 ngày. Sau đó archive sang S3 (compliance/forensic).
```

---

## 2. AuditLog Model (đã spec ở 10_SECURITY.md)

Xem `10_SECURITY.md` section "Audit Log Schema".

---

## 3. CircuitBreakerState Model (persist state qua restart)

```python
# src/models/circuit_breaker_state.py
class CircuitBreakerState(Base):
    """Persist state để worker mới boot không reset breaker."""
    __tablename__ = "circuit_breaker_state"

    service         = Column(String(50), primary_key=True)   # odoo | shopee
    state           = Column(String(20), nullable=False)     # closed|open|half_open
    failure_count   = Column(Integer, default=0)
    success_count   = Column(Integer, default=0)
    opened_at       = Column(DateTime(timezone=True))
    last_failure_at = Column(DateTime(timezone=True))
    updated_at      = Column(DateTime(timezone=True), onupdate=func.now())
```

---

## 4. Composite Indexes (performance)

```sql
-- order_mapping: query Admin UI search + filter
CREATE INDEX idx_order_mapping_platform_status_created
    ON order_mapping (platform, status, created_at DESC);

CREATE INDEX idx_order_mapping_status_updated
    ON order_mapping (status, updated_at DESC)
    WHERE status IN ('failed', 'dead_letter');

CREATE INDEX idx_order_mapping_platform_order_id_trgm
    ON order_mapping USING gin (platform_order_id gin_trgm_ops);
-- Cần: CREATE EXTENSION pg_trgm; — cho ILIKE %search% nhanh

-- order_sync_log: tránh phình
CREATE INDEX idx_sync_log_mapping_created
    ON order_sync_log (order_mapping_id, created_at DESC);

-- product_mapping: lookup theo SKU
CREATE INDEX idx_product_mapping_odoo_sku
    ON product_mapping (odoo_sku) WHERE is_active = true;

-- Partial index outbox (đã có ở 03, confirm)
CREATE INDEX idx_outbox_pending_oldest
    ON webhook_outbox (process_after ASC)
    WHERE status IN ('pending', 'failed');
```

### Retention policies (TTL cleanup job 3:00 AM)

```python
RETENTION_DAYS = {
    "webhook_outbox":     7,   # status=published
    "webhook_event_log":  30,
    "order_sync_log":     90,
    "reconciliation_log": 180,
    "price_sync_log":     180,
    "audit_log":          365,
}

# Archive to S3 trước khi delete:
#   - webhook_event_log sau 30d → S3 Parquet
#   - audit_log sau 90d → S3 Parquet
```

---

## 5. Bundle Stock Logic (combo SKU)

### Vấn đề
1 SKU trên sàn = N SKU Odoo (combo). Khi sync stock, lấy min của tất cả components / quantity.

```python
# src/services/stock_service.py
async def calculate_bundle_stock(platform_sku: str, platform: str) -> int:
    """
    Bundle: lấy min(component_stock / component_qty) across all components.
    Ví dụ: bundle "Combo3" = 2x SKU-A + 1x SKU-B
           SKU-A có 100 → cho phép 50 bundle
           SKU-B có 30  → cho phép 30 bundle
           → bundle stock = min(50, 30) = 30
    """
    mapping = await get_product_mapping(platform_sku, platform)
    if mapping.mapping_type == "simple":
        return await calculate_simple_stock(mapping.odoo_sku, platform)

    components = await get_bundle_components(mapping.id)
    bundle_qtys = []
    for comp in components:
        component_stock = await odoo.get_stock_quantity(comp.odoo_sku)
        # Apply allocation + buffer cho COMPONENT, không phải bundle
        allocated = apply_allocation(component_stock, comp.odoo_sku, platform)
        bundle_qtys.append(allocated // comp.quantity)

    return max(0, min(bundle_qtys))
```

### Khi đơn bundle được tạo trên sàn

```python
# Order line trên Odoo: KHÔNG tạo 1 line cho bundle SKU
# → Tạo N lines cho từng component (mỗi line nhân theo quantity in bundle)
def expand_bundle_order_lines(item: UnifiedOrderItem) -> list[dict]:
    mapping = get_product_mapping(item.platform_sku_id, platform)
    if mapping.mapping_type == "simple":
        return [{"sku": mapping.odoo_sku, "qty": item.quantity, "price": item.discounted_price}]

    components = get_bundle_components(mapping.id)
    # Tổng giá bundle phân bổ theo tỉ lệ giá Odoo của từng component
    odoo_prices = {c.odoo_sku: odoo.get_product_price(c.odoo_sku) for c in components}
    total_odoo  = sum(odoo_prices[c.odoo_sku] * c.quantity for c in components)
    return [
        {
            "sku":   c.odoo_sku,
            "qty":   c.quantity * item.quantity,
            "price": float(item.discounted_price) * (odoo_prices[c.odoo_sku] / total_odoo),
        }
        for c in components
    ]
```

### Edge cases bundle

```
- 1 component out of stock → bundle stock = 0 (block hết).
- Component có flash sale lock → bundle stock = 0 với platform đó.
- Update component stock → trigger recalc cho TẤT CẢ bundle chứa component đó (cache invalidate).
- Reverse mapping: cache `component_sku → [bundle_ids]` trong Redis TTL 1h.
```

---

## 6. Circuit Breaker Implementation

```python
# src/core/circuit_breaker.py
import time
from enum import Enum
from typing import Callable, Awaitable
import structlog

logger = structlog.get_logger(__name__)


class CBState(str, Enum):
    CLOSED    = "closed"
    OPEN      = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """
    Async circuit breaker với state persist DB (load on init).
    Threshold default: 5 failures trong 60s → open.
    Open timeout: 60s → half_open → 1 success → closed.
    """

    def __init__(
        self,
        service: str,
        failure_threshold: int = 5,
        rolling_window: int = 60,
        open_timeout: int = 60,
        half_open_success_required: int = 1,
    ):
        self.service = service
        self.failure_threshold = failure_threshold
        self.rolling_window = rolling_window
        self.open_timeout = open_timeout
        self.half_open_success = half_open_success_required

        self._state = CBState.CLOSED
        self._failures: list[float] = []
        self._opened_at: float | None = None
        self._half_open_passes = 0

    async def call(self, fn: Callable[..., Awaitable], *args, **kwargs):
        await self._transition_check()

        if self._state == CBState.OPEN:
            raise CircuitOpenError(f"Circuit {self.service} OPEN")

        try:
            result = await fn(*args, **kwargs)
        except Exception as e:
            await self._on_failure(e)
            raise

        await self._on_success()
        return result

    async def _transition_check(self):
        now = time.time()
        if self._state == CBState.OPEN and now - self._opened_at >= self.open_timeout:
            self._state = CBState.HALF_OPEN
            self._half_open_passes = 0
            logger.info("circuit_breaker_half_open", service=self.service)
            METRICS.cb_state.labels(service=self.service).set(2)

    async def _on_failure(self, exc: Exception):
        now = time.time()
        self._failures = [t for t in self._failures if now - t < self.rolling_window]
        self._failures.append(now)

        if self._state == CBState.HALF_OPEN:
            self._open(now)
            return

        if len(self._failures) >= self.failure_threshold:
            self._open(now)

    async def _on_success(self):
        if self._state == CBState.HALF_OPEN:
            self._half_open_passes += 1
            if self._half_open_passes >= self.half_open_success:
                self._close()
        elif self._state == CBState.CLOSED:
            self._failures.clear()

    def _open(self, now: float):
        self._state = CBState.OPEN
        self._opened_at = now
        logger.warning("circuit_breaker_open", service=self.service,
                       failures=len(self._failures))
        METRICS.cb_state.labels(service=self.service).set(1)
        METRICS.cb_open_total.labels(service=self.service).inc()

    def _close(self):
        self._state = CBState.CLOSED
        self._failures.clear()
        self._opened_at = None
        logger.info("circuit_breaker_closed", service=self.service)
        METRICS.cb_state.labels(service=self.service).set(0)


class CircuitOpenError(Exception): ...

# Usage:
odoo_breaker = CircuitBreaker("odoo", failure_threshold=5, open_timeout=60)

async def safe_odoo_call(method: str, *args):
    return await odoo_breaker.call(odoo_client.execute, method, *args)
```

---

## 7. Reconciliation Field Drift (mở rộng v2.0)

V2.0 reconciliation chỉ check missing orders. Bổ sung check drift:

```python
async def check_field_drift(platform: str, date: date) -> list[Drift]:
    """
    So sánh fields quan trọng giữa platform và Odoo cho orders đã sync.
    Phát hiện trường hợp update bị miss.
    """
    drifts = []
    synced = await get_synced_orders(platform, date)

    for mapping in synced:
        platform_order = await connector.get_order_detail(mapping.platform_order_id)
        odoo_order     = await odoo.read_sale_order(mapping.odoo_order_id)

        # Check shipping status
        platform_status = SHOPEE_STATUS_MAP[platform_order.status]
        if platform_status != map_odoo_status(odoo_order.state):
            drifts.append(Drift(
                mapping=mapping, field="status",
                platform_value=platform_status,
                odoo_value=odoo_order.state,
            ))

        # Check tracking number
        if platform_order.logistics.tracking_number != odoo_order.x_tracking_number:
            drifts.append(Drift(mapping=mapping, field="tracking_number", ...))

        # Check total amount (tolerance 1000 VND)
        if abs(platform_order.total_amount - odoo_order.amount_total) > 1000:
            drifts.append(Drift(mapping=mapping, field="total_amount", ...))

    # Auto-fix status drift, alert cho phần còn lại
    for d in drifts:
        if d.field == "status" and d.platform_value > d.odoo_value:
            await OrderService.update_status(d.mapping, d.platform_value)
        else:
            await AlertService.send_drift_alert(d)

    return drifts
```

Schedule: chạy chung với reconciliation 2:00 AM.

---

## 8. Token Refresh Inline Fallback

V2.0 chỉ refresh qua Beat 3h. Thêm inline fallback khi 401:

```python
# src/connectors/shopee/client.py
async def _request(self, method, path, **kwargs):
    for attempt in range(2):
        sign, ts = self._sign(path)
        params = {**kwargs.get("params", {}), "sign": sign, "timestamp": ts,
                  "partner_id": self.partner_id, "shop_id": self.shop_id,
                  "access_token": self._access_token}
        try:
            resp = await self.http.request(method, path, params=params)
            data = resp.json()
            if data.get("error") == "error_auth_token_expired" and attempt == 0:
                logger.warning("shopee_token_expired_inline_refresh")
                await self._refresh_token()    # Inline refresh
                continue
            return data
        except httpx.HTTPError as e:
            raise
```

Đồng thời:
- Beat job mỗi 3h refresh proactively (default path).
- Inline refresh chỉ kích hoạt khi Beat down hoặc race condition.
- Lock Redis (`SET NX EX 60`) tránh nhiều worker refresh cùng lúc.

---

## 9. Partner Merge Safeguard

V2.0 gộp partner theo phone duy nhất → risk gộp nhầm shop B2B nhiều khách dùng số tổng đài.

```python
# src/odoo/client.py
def get_or_create_partner(self, address: UnifiedAddress, platform: str,
                          buyer_platform_id: str) -> int:
    """
    Match theo (phone, name_similarity > 0.8) thay vì chỉ phone.
    Fallback: tạo mới với suffix platform để tránh gộp nhầm.
    """
    phone = normalize_vn_phone(address.phone)
    candidates = self._search_read("res.partner",
        [["phone", "=", phone]], ["id", "name"], limit=10)

    if not candidates:
        return self._create_partner(address, platform, buyer_platform_id)

    # Fuzzy match name
    from rapidfuzz import fuzz
    for c in candidates:
        if fuzz.ratio(c["name"].lower(), address.full_name.lower()) > 80:
            return c["id"]

    # Phone trùng nhưng tên khác hẳn → tạo mới với suffix
    logger.warning("partner_phone_collision_create_new",
                   phone=phone, existing_count=len(candidates))
    address.full_name = f"{address.full_name} [{platform}:{buyer_platform_id[:8]}]"
    return self._create_partner(address, platform, buyer_platform_id)
```

Bổ sung: KHÔNG set `customer_rank=1` cho mọi partner — chỉ set khi cần (B2C). Mặc định 0.

---

## 10. Worker Concurrency Tuning

V2.0: concurrency=4 không đủ cho flash sale 500/60s khi Odoo P95 = 3s.

```
Tính toán:
  Throughput cần:  500 đơn / 60s = 8.3 đơn/s
  Odoo P95:        3s/call
  Workers cần:     8.3 × 3 = ~25 concurrent workers

Recommendation:
  worker-high:     concurrency=8, replicas=2  → 16 workers
  worker-normal:   concurrency=8, replicas=2  → 16 workers
  worker-low:      concurrency=2, replicas=1  → 2 workers (reconciliation chỉ chạy 2AM)

Total: 34 workers. Mỗi worker XML-RPC connection riêng (pool size = 1) qua PgBouncer transaction mode.
```

### Worker config bắt buộc

```python
# src/workers/app.py
app.conf.update(
    task_acks_late=True,                       # Re-deliver nếu worker crash
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,              # Fair scheduling
    task_track_started=True,
    task_time_limit=300,                       # 5 phút hard timeout
    task_soft_time_limit=240,                  # 4 phút soft timeout
    broker_connection_retry_on_startup=True,
    result_expires=86400,                      # 1 ngày
)
```

---

## 11. Idempotency Key cho Celery Tasks

Outbox đã có idempotency qua `task_id=f"outbox-{id}"`. Bổ sung cho task không qua outbox:

```python
# src/workers/order_worker.py
@app.task(bind=True, max_retries=5)
def sync_order_to_odoo(self, order_mapping_id: int):
    lock_key = f"lock:sync_order:{order_mapping_id}"
    if not redis.set(lock_key, "1", ex=300, nx=True):
        logger.info("sync_order_already_running", id=order_mapping_id)
        return {"status": "skipped_locked"}
    try:
        ...
    finally:
        redis.delete(lock_key)
```

---

## 12. Health Check chi tiết

```python
# src/monitoring/health.py
async def check_all() -> dict:
    return {
        "postgres":      await check_postgres(),       # SELECT 1 timeout 2s
        "postgres_replica": await check_replica_lag(), # pg_last_wal_replay_lsn diff < 30s
        "redis":         await check_redis(),          # PING + INFO memory < 80%
        "odoo":          await check_odoo(),           # /web/health hoặc XML-RPC version
        "celery":        await check_celery_workers(), # inspect().active() count > 0
        "outbox_lag":    await check_outbox_lag(),     # oldest pending < 60s
        "circuit_odoo":  cb_odoo.state.value,
        "shopee_token":  await check_shopee_token(),   # age < 3.5h, refresh < 7d
    }

# /ready trả 503 nếu BẤT KỲ check critical fail.
# /health (liveness) chỉ check process còn sống.
```

---

## 13. Performance Budget

| Operation | Target P95 | Hard limit |
|---|---|---|
| Webhook receive → 200 OK | 200ms | 500ms |
| Outbox insert | 50ms | 100ms |
| Order sync E2E (webhook → Odoo confirmed) | 3 phút | 5 phút |
| Stock sync (Odoo change → sàn) | 90s | 3 phút |
| Admin UI page render | 800ms | 2s |
| Reconciliation full day | 15 phút | 30 phút |

Khi vượt hard limit → alert critical.
