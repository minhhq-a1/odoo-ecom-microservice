# ARCHITECTURE CONTEXT
## System Design & Component Relationships

---

## High-Level Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                        MARKETPLACE LAYER                          │
│                                                                    │
│   ┌─────────────┐   ┌─────────────┐   ┌──────────────────┐      │
│   │  Shopee API │   │ Lazada API  │   │ TikTok Shop API  │      │
│   │  (Phase 1)  │   │  (Phase 2)  │   │    (Phase 2)     │      │
│   └──────┬──────┘   └──────┬──────┘   └────────┬─────────┘      │
└──────────┼────────────────┼────────────────────┼────────────────┘
           │                │                    │
           ▼                ▼                    ▼
┌──────────────────────────────────────────────────────────────────┐
│                     MIDDLEWARE (Self-hosted)                       │
│                                                                    │
│  ┌──────────────────────────────────────────────────────────┐    │
│  │                    FastAPI Application                    │    │
│  │   /webhook/{platform}  │  /admin/*  │  /health           │    │
│  │   Rate limit: 100 req/s per platform (inbound)           │    │
│  └──────────────────────────┬───────────────────────────────┘    │
│                              │                                     │
│  ┌───────────────────────────▼───────────────────────────────┐   │
│  │           Transactional Outbox (PostgreSQL)                │   │
│  │   Lưu webhook vào DB TRƯỚC khi push queue                 │   │
│  │   Relay job mỗi 30s → đảm bảo không mất event            │   │
│  └───────────────────────────┬───────────────────────────────┘   │
│                              │                                     │
│  ┌───────────────────────────▼───────────────────────────────┐   │
│  │                   Connector Layer                          │   │
│  │  ShopeeConnector  │  LazadaConnector  │  TikTokConnector  │   │
│  │  - Token refresh  │  - Signature      │  - OAuth2         │   │
│  │  - Rate limiting  │  - Pagination     │  - Pagination     │   │
│  └───────────────────────────┬───────────────────────────────┘   │
│                              │                                     │
│  ┌───────────────────────────▼───────────────────────────────┐   │
│  │                 Normalization Layer                        │   │
│  │     Raw Platform Data  →  UnifiedOrder / UnifiedProduct    │   │
│  └───────────────────────────┬───────────────────────────────┘   │
│                              │                                     │
│  ┌───────────────────────────▼───────────────────────────────┐   │
│  │               Redis Message Queue                          │   │
│  │   queue:orders.created  │  queue:orders.updated           │   │
│  │   queue:stock.sync      │  queue:shipment.confirm         │   │
│  │   queue:price.sync      │  queue:reconciliation           │   │
│  └───────────────────────────┬───────────────────────────────┘   │
│                              │                                     │
│  ┌───────────────────────────▼───────────────────────────────┐   │
│  │               Celery Workers (scalable)                    │   │
│  │   OrderWorker  │  StockWorker  │  ShipmentWorker           │   │
│  │   PriceWorker  │  ReconciliationWorker                     │   │
│  └───────────────────────────┬───────────────────────────────┘   │
│                              │                                     │
│  ┌───────────────────────────▼───────────────────────────────┐   │
│  │               Odoo Integration Layer                       │   │
│  │   OdooClient (XML-RPC)  │  OdooRESTClient (REST API)      │   │
│  └───────────────────────────┬───────────────────────────────┘   │
└─────────────────────────────┼────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│                          ODOO 17                                  │
│   Sale Orders  │  Inventory  │  Products  │  Partners            │
└──────────────────────────────────────────────────────────────────┘
```

---

## Database Architecture

### Middleware PostgreSQL

```sql
-- Core Tables

order_mapping           -- Platform order ↔ Odoo order mapping
order_sync_log          -- Toàn bộ lịch sử sync, dùng để debug
product_mapping         -- Platform SKU ↔ Odoo product mapping
stock_allocation_config -- Cấu hình phân bổ tồn kho per SKU per sàn
platform_config         -- Credentials và config cho từng sàn
webhook_outbox          -- Transactional outbox (đảm bảo không mất event)
webhook_event_log       -- Raw webhook events (audit trail)
reconciliation_log      -- Kết quả nightly reconciliation job
price_sync_log          -- Lịch sử sync giá Odoo → sàn
```

### ⚠️ PostgreSQL High Availability

```
Production bắt buộc:
  - 1 Primary + 1 Read Replica (streaming replication)
  - Backup tự động mỗi 1 giờ → S3-compatible storage
  - Point-in-time recovery (PITR) giữ 7 ngày
  - Connection pooling với PgBouncer (giảm connection overhead)

PostgreSQL là SPOF nếu không có HA.
Nếu Postgres down → API không nhận webhook → Outbox không hoạt động.
```

### Quan hệ giữa các bảng

```
platform_config (1) ──── (N) order_mapping
product_mapping (1) ──── (N) stock_allocation_config
order_mapping   (1) ──── (N) order_sync_log
webhook_event_log ──────────► order_mapping (via platform_order_id)
```

---

## Queue Architecture

### Queue Names & Routing

```
HIGH PRIORITY:
  queue:orders.created.high    → Flash sale / VIP orders
  queue:shipment.confirm       → Xác nhận vận chuyển (time-sensitive)

NORMAL PRIORITY:
  queue:orders.created.normal  → Đơn thường
  queue:orders.updated         → Cập nhật trạng thái đơn
  queue:stock.sync             → Đồng bộ tồn kho
  queue:price.sync             → Sync giá Odoo → sàn

LOW PRIORITY:
  queue:reconciliation         → Nightly reconciliation job
  queue:reports                → Báo cáo, analytics
  queue:cleanup                → Dọn dẹp log cũ
```

### Retry Policy

```
Attempt 1: Ngay lập tức
Attempt 2: Sau 60 giây
Attempt 3: Sau 5 phút
Attempt 4: Sau 30 phút
Attempt 5: Sau 2 giờ
Dead Letter: Chuyển vào queue:dead_letter → Alert team
```

---

## Authentication Flow

### Shopee OAuth

```
1. Middleware lưu: partner_id, partner_key, shop_id
2. Lấy access_token + refresh_token lần đầu (manual)
3. access_token expire sau 4 giờ → auto refresh trước khi hết hạn
4. refresh_token expire sau 30 ngày → alert để renew thủ công
5. Token lưu trong Redis với TTL tương ứng
```

### Request Signing (Shopee)

```python
# HMAC-SHA256
sign_string = f"{partner_id}{api_path}{timestamp}{access_token}{shop_id}"
signature   = hmac_sha256(partner_key, sign_string)
```

---

## Stock Sync Strategy

### Vấn đề cần giải quyết
- Tránh oversell khi nhiều sàn cùng bán 1 SKU
- Xử lý spike khi flash sale

### Giải pháp: Buffer + Allocation

```
Odoo actual stock: 100 units
Buffer (10%):       10 units  ← KHÔNG sync lên sàn
Available:          90 units

Phân bổ:
  Shopee:  45 units (50%)
  Lazada:  27 units (30%)
  TikTok:  18 units (20%)
```

### Trigger sync tồn kho
1. Odoo stock.move created/done → webhook → middleware
2. Sau mỗi đơn hàng confirmed thành công
3. Scheduled job: mỗi 15 phút (safety net)

---

## Monitoring Architecture

```
Celery Workers ──► Flower (port 5555)     → Task monitoring
FastAPI        ──► Prometheus (port 9090) → Metrics
PostgreSQL     ──► pg_stat                → DB metrics
All metrics    ──► Grafana (port 3000)    → Dashboard

Alerts:
  - Dead letter queue > 0          → Slack/Email ngay lập tức
  - Queue depth > 500              → Warning
  - Sync lag > 5 phút              → Warning
  - Worker down                    → Critical alert
  - Token sắp expire (< 1h)        → Warning
  - Admin retry endpoint: max 1 lần/phút (rate limited)
  - Reconciliation tìm thấy miss   → Warning report
  - PostgreSQL replica lag > 30s   → Warning
  - Redis memory > 80%             → Warning
```

---

## Deployment Architecture

```yaml
# Docker Compose Services
services:
  api:             FastAPI app (port 8000)
  worker-high:     Celery worker - high priority queues
  worker-normal:   Celery worker - normal queues
  worker-low:      Celery worker - low priority + reconciliation
  beat:            Celery Beat scheduler
  redis:           Redis 7 (port 6379) — AOF + RDB persistence
  postgres:        PostgreSQL 16 primary (port 5432)
  postgres-replica: PostgreSQL read replica (streaming replication)
  flower:          Celery monitor (port 5555)
  prometheus:      Metrics (port 9090)
  grafana:         Dashboard (port 3000)

# Scheduled Jobs (Celery Beat)
  outbox-relay:    Mỗi 30 giây — push outbox pending vào queue
  token-refresh:   Mỗi 3 giờ   — refresh Shopee access token
  stock-sync:      Mỗi 15 phút — safety net stock sync
  reconciliation:  Mỗi 2:00 AM — so sánh Shopee ↔ Odoo
  price-sync:      Mỗi 6 giờ   — sync giá Odoo → sàn (nếu bật)
  cleanup:         Mỗi 3:00 AM — xóa outbox/log cũ > 7 ngày
```
