# PROJECT OVERVIEW
## Odoo E-Commerce Middleware Integration

---

## Mục tiêu dự án

Xây dựng middleware layer tự phát triển (không phụ thuộc vendor) để tích hợp Odoo ERP với các sàn thương mại điện tử Việt Nam, xử lý ~3.000 đơn hàng/ngày.

---

## Phạm vi dự án

| Hạng mục | Chi tiết |
|---|---|
| **ERP** | Odoo 18 (self-hosted hoặc Odoo.sh) |
| **Sàn TMĐT Phase 1** | Shopee |
| **Sàn TMĐT Phase 2** | Lazada, TikTok Shop |
| **Quy mô** | ~3.000 đơn/ngày (~2 đơn/phút trung bình, spike cao hơn khi flash sale) |
| **Môi trường** | Production: Linux Ubuntu 22.04 LTS |

---

## Tech Stack

### Middleware
| Layer | Technology |
|---|---|
| Web Framework | **FastAPI** (Python 3.12+) |
| Task Queue | **Celery 5.x** |
| Message Broker | **Redis 7.x** |
| Database | **PostgreSQL 16** |
| ORM | **SQLAlchemy 2.x** + Alembic |
| HTTP Client | **httpx** (async) |
| Validation | **Pydantic v2** |
| Containerization | **Docker** + Docker Compose |
| Monitoring | **Flower** (Celery), **Prometheus** + **Grafana** |

### Odoo Integration
| Phương thức | Mục đích |
|---|---|
| XML-RPC | CRUD operations (orders, products, partners) |
| REST API (Odoo 18) | Thay thế XML-RPC cho các endpoint mới |
| Webhook (outbound) | Odoo → Middleware khi tồn kho thay đổi |

---

## Nguyên tắc thiết kế

1. **Idempotent**: Mọi operation có thể retry an toàn, không tạo duplicate
2. **Fault Tolerant**: Lỗi ở 1 sàn không ảnh hưởng sàn khác
3. **Async First**: Ưu tiên xử lý bất đồng bộ, không block request
4. **Observable**: Mọi action đều được log, có thể trace và alert
5. **Configurable**: Business logic (tỷ lệ tồn kho, buffer...) cấu hình qua DB, không hardcode
6. **Safe by Default**: Dry-run mode cho go-live, không bao giờ push negative stock
7. **Self-healing**: Reconciliation job ban đêm tự phát hiện và vá các đơn bị miss

---

## Luồng dữ liệu chính

```
[Shopee/Lazada/TikTok]
    → Webhook / Polling
    → Connector Layer (auth, rate limit, retry)
    → Normalization Layer (unified schema)
    → Redis Queue
    → Celery Worker
    → Odoo Integration Layer (XML-RPC)
    → Odoo 18
```

```
[Odoo 18]
    → Stock change event
    → Middleware stock sync service
    → Phân bổ tồn kho theo cấu hình
    → Đồng thời update lên tất cả sàn
```

---

## Các module chính

| Module | Chức năng |
|---|---|
| `connectors/` | Connector cho từng sàn (Shopee, Lazada, TikTok) |
| `transformers/` | Normalize data từng sàn → Unified Schema |
| `workers/` | Celery tasks xử lý đơn hàng, tồn kho |
| `odoo/` | Client tương tác với Odoo |
| `api/` | FastAPI endpoints (webhook receiver, admin API) |
| `models/` | SQLAlchemy models (mapping, log, config) |
| `monitoring/` | Health check, metrics, alerting |
| `services/reconciliation/` | Nightly job so sánh Shopee ↔ Odoo, tìm đơn bị miss |
| `services/price_sync/` | Sync giá từ Odoo → sàn (khi Odoo là price master) |

---

## Môi trường

```
Development:  Docker Compose local
Staging:      Replica THẬT SỰ của Production (cùng Odoo version, data volume, network)
Production:   Docker Compose hoặc K8s (tùy quy mô team)
```

> ⚠️ **Staging phải mirror production** — cùng Odoo version, cùng data volume, cùng network
> latency. Đây là yêu cầu bắt buộc, không phải tùy chọn.

---

## Chiến lược go-live

```
Bước 1: Chạy Dry-run mode (MIDDLEWARE_DRY_RUN=true)
        → Nhận webhook, transform data, log kết quả
        → KHÔNG tạo order trên Odoo
        → Kiểm tra mapping SKU, customer data

Bước 2: Load test trên Staging
        → Mô phỏng 500 webhook đồng thời (flash sale scenario)
        → Đo response time Odoo, queue drain time, memory

Bước 3: Go-live với shadow mode
        → Tạo order thật nhưng so sánh với tạo thủ công
        → Theo dõi sát 48h đầu

Bước 4: Full production
        → Bật tất cả features, bật Reconciliation job
```
