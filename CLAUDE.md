# CLAUDE.md
## Master Context — Odoo E-Commerce Middleware Project

> **Đây là file entry point cho AI assistant.**
> Đọc file này trước, sau đó đọc các file context liên quan đến task cụ thể.

---

## Dự án là gì?

Middleware layer tự phát triển bằng **Python (FastAPI + Celery)** để tích hợp **Odoo 18** với các sàn thương mại điện tử Việt Nam, bắt đầu với **Shopee**, xử lý ~**3.000 đơn/ngày**.

**Nguyên tắc cốt lõi:**
- Không phụ thuộc vendor bên thứ ba
- Idempotent — mọi operation retry-safe
- Async-first — không block
- Observable — mọi thứ đều được log
- Safe by Default — dry-run mode, không bao giờ push negative stock
- Self-healing — Reconciliation job ban đêm tự vá đơn bị miss

---

## Map tài liệu context

| File | Đọc khi nào |
|---|---|
| `01_PROJECT_OVERVIEW.md` | Tổng quan, tech stack, scope, chiến lược go-live |
| `02_ARCHITECTURE.md` | Thiết kế hệ thống, outbox, queue, DB HA, scheduled jobs |
| `03_DATA_MODELS.md` | Schema, DB models (gồm outbox, reconciliation, price sync) |
| `04_SHOPEE_API.md` | Shopee API, auth, webhook, rate limits |
| `05_ODOO_INTEGRATION.md` | XML-RPC, custom fields, Odoo client |
| `06_PROJECT_STRUCTURE.md` | Cấu trúc thư mục, conventions, patterns |
| `07_BUSINESS_RULES.md` | Nghiệp vụ, edge cases, dry-run, reconciliation, price sync |
| `08_ENVIRONMENT_DEPLOYMENT.md` | Docker, env vars, Redis/PgBouncer config, load test |
| `09_TESTING.md` | Unit/integration/contract/load/chaos test strategy + fixtures |
| `10_SECURITY.md` | STRIDE threat model, encryption, key rotation, RBAC, OWASP |
| `11_OBSERVABILITY.md` | SLO/SLI, Prometheus metrics catalog, dashboards, alerts |
| `12_CICD.md` | GitHub Actions, blue-green, migration safety, rollback |
| `13_GAP_PATCHES.md` | v2.1 gap fixes: webhook_event_log, bundle stock, breaker, indexes |

---

## Quick Reference

### Tech Stack
```
FastAPI 0.115  +  Celery 5.4  +  Redis 7  +  PostgreSQL 16
Python 3.12    +  SQLAlchemy 2  +  Pydantic v2  +  httpx
PgBouncer (connection pooling)  +  slowapi (rate limiting)
```

### Entry Points
```
API Server:   src/api/main.py
Celery App:   src/workers/app.py
Config:       src/core/config.py
```

### Key Patterns

**Khi tạo một connector mới:**
1. Kế thừa `BaseConnector` từ `src/connectors/base.py`
2. Implement tất cả abstract methods
3. Tạo transformer tương ứng trong `src/transformers/`
4. Đăng ký webhook route trong `src/api/routers/webhooks.py`

**Khi tạo một Celery task:**
1. Luôn dùng `bind=True` để có `self.retry()`
2. Implement idempotency check ở đầu task
3. Log với structlog, include context (platform, order_id)
4. Phân loại lỗi: retryable vs dead_letter
5. Kiểm tra dry-run mode trước khi gọi Odoo

**Khi tương tác với Odoo:**
1. Dùng `OdooClient` từ `src/odoo/client.py`
2. Kiểm tra `settings.MIDDLEWARE_DRY_RUN` trước khi write
3. Cache product mapping trong Redis (TTL 1h)
4. Kiểm tra duplicate trước khi create

**Khi nhận webhook từ sàn:**
1. Verify signature ngay lập tức
2. Lưu vào `webhook_outbox` (PostgreSQL) TRƯỚC KHI làm gì khác
3. Trả 200 OK cho sàn ngay
4. Background task thử push vào Redis (best-effort)
5. Relay job 30s sẽ xử lý nếu Redis chưa sẵn sàng

---

## Conventions nhanh

```python
# ✅ Async cho FastAPI/connectors
async def get_orders(self) -> List[UnifiedOrder]: ...

# ✅ Sync cho Celery tasks
@app.task(bind=True, max_retries=5)
def sync_order(self, order_data: dict): ...

# ✅ Dry-run check (BẮT BUỘC trước mọi write operation)
if settings.MIDDLEWARE_DRY_RUN:
    logger.info("dry_run_skip", action="create_order", ...)
    return

# ✅ Logging
logger.info("event_name", key1=val1, key2=val2)

# ✅ Error handling
except ShopeeRateLimitError as e:
    raise self.retry(countdown=e.retry_after)
except OdooValidationError as e:
    log_dead_letter(...)  # Không retry

# ✅ Phone normalize
phone = normalize_vn_phone(raw_phone)  # → "0912345678"

# ✅ Stock calculation (không bao giờ âm)
qty = max(0, int(odoo_stock * (1 - buffer_pct) * allocation_pct))
```

---

## Trạng thái dự án hiện tại

```
Phase 1 (Shopee):
  ✅ Architecture & context docs (v2.1 — gap patches applied)
  ✅ Outbox Pattern (implementation guide + scaffold)
  ✅ Admin UI (FastAPI + Jinja2 scaffold + audit log)
  ✅ Runbook (Ops team)
  ✅ Dry-run mode (env + per-platform flag)
  ✅ ShopeeConnector (auth + signing + circuit breaker + update_stock wired)
  ✅ ShopeeTransformer (status map, line items, phone normalize)
  ✅ Celery workers (order/stock/shipment/price + beat schedule)
  ✅ OdooClient (XML-RPC + breaker + partner fuzzy match)
  ✅ FastAPI (webhooks + admin + health + metrics + replay protection)
  ✅ Database migrations 001-005 (11 tables + roles + indexes)
  ✅ Docker setup + PgBouncer + compose
  ✅ Test scaffold (conftest + fixtures + unit tests)
  ✅ CI workflow (lint + test + security + build)
  ✅ stock_safety_net wired (ProductMapping iteration via MappingService)
  ✅ ReconciliationService logic (missing + auto_fix + field drift)
  ✅ Load test harness (4 locustfiles + verifier + orchestrator script)
  ✅ Load test executed locally — 4/4 scenarios pass, 8159 webhooks, 0 data loss
     → results/LOAD_TEST_REPORT.md, results/*.csv
  ✅ Replay-nonce fail-open hardening (test_webhook_replay.py — 4 tests pass)
  ✅ Odoo 18 connectivity verified (test_odoo18_live.py — 4 tests pass against
     real Odoo 18.0 container — XML-RPC paths stable, no client.py change needed)
  ✅ Shopee OAuth flow (gen_auth_url.py + exchange_token.py + admin endpoints +
     Fernet-encrypted DB backup — test_shopee_oauth.py 10 tests pass, live URL
     + endpoints + Redis store + DB encrypt verified end-to-end)
  ✅ Admin web UI cấu hình (server-rendered Jinja2):
       /admin/config/platforms      — bật/tắt sàn, dry_run, price_master
       /admin/config/shopee         — OAuth wizard 1-click + token countdown
       /admin/config/products       — product mapping + bundle components UI
       /admin/config/stock          — stock allocation (buffer + alloc %) inline form
  ✅ Odoo 18 custom-fields addon (`a1_sale_ecom_middleware`) tại
     `~/odoo-workspace/18.0/extra-addons/onnet-dc7-internal/addons/custom/`:
       sale.order  : x_platform, x_platform_order_id (UNIQUE), x_platform_order_sn,
                     x_sync_status, x_tracking_number + Python pair-constraint
       res.partner : x_platform_source, x_platform_buyer_id
       product.product: x_marketplace_buffer_pct, x_block_marketplace_sync
       branch: feat/a1_sale_ecom_middleware, commit 00133c4e5
       Live-tested: 10/10 Odoo tests pass on Odoo 18.0-20260504 (0.47s, 602 queries),
       UNIQUE constraint blocks duplicate, search-by-platform works.
       audit_log auto-write mọi save/delete (actor, ip, payload, timestamp)
  ⏳ Go-live  — sign-off business decision (Ops + QA + stakeholder),
                not a code-readiness gate. Tech ready: 100%.

Phase 2 (Lazada + TikTok):
  🔲 LazadaConnector + TikTokConnector
  🔲 Multi-platform stock allocation
  🔲 Reconciliation job (toàn bộ sàn)
  🔲 Price sync (nếu Odoo là master)
  🔲 Grafana dashboards
  🔲 PostgreSQL read replica
```

---

## Câu hỏi thường gặp

**Q: Tại sao dùng XML-RPC thay REST cho Odoo?**
A: XML-RPC stable hơn, community support tốt. Odoo 18 vẫn giữ XML-RPC tương thích ngược; REST API Odoo 18 đã mature hơn, dùng cho endpoint mới nếu cần.

**Q: Tại sao Redis làm cả broker lẫn cache?**
A: Đơn giản hóa infrastructure. Dùng database khác nhau (0=cache, 1=celery broker, 2=celery result).

**Q: Webhook miss thì sao?**
A: 3 lớp bảo vệ: (1) Outbox Pattern — lưu DB trước khi push queue, (2) Polling fallback mỗi 10 phút, (3) Reconciliation job lúc 2:00 AM.

**Q: Dry-run là gì và khi nào dùng?**
A: Set `MIDDLEWARE_DRY_RUN=true` để nhận webhook thật, transform data thật, nhưng không write vào Odoo/sàn. Dùng khi go-live lần đầu để kiểm tra mapping.

**Q: Price master ở đâu?**
A: Default là platform (giá set trên Shopee). Nếu muốn Odoo là master, set `PRICE_MASTER=odoo` và bật `ENABLE_PRICE_SYNC=true`. Xem `07_BUSINESS_RULES.md` section 13.

**Q: Reconciliation job làm gì?**
A: Mỗi đêm 2:00 AM, lấy danh sách đơn từ Shopee API, so sánh với Odoo, tự động sync các đơn bị miss, alert Slack các đơn cần xem xét thủ công. Xem `07_BUSINESS_RULES.md` section 12.

**Q: PostgreSQL SPOF thì xử lý thế nào?**
A: Production cần 1 Primary + 1 Read Replica + backup mỗi 1h. PgBouncer đứng trước để connection pooling. Chi tiết xem `02_ARCHITECTURE.md` và `08_ENVIRONMENT_DEPLOYMENT.md`.

**Q: Admin retry-all bị bấm liên tục thì sao?**
A: Rate limited bằng slowapi: max 1 lần/phút per IP. Xem `07_BUSINESS_RULES.md` section 14.
