# PROJECT STRUCTURE & CODING CONVENTIONS
## Directory Layout, Naming Rules, Code Patterns

---

## Directory Structure

```
ecommerce-middleware/
│
├── src/
│   ├── api/                        # FastAPI application
│   │   ├── __init__.py
│   │   ├── main.py                 # FastAPI app entry point
│   │   ├── dependencies.py         # DI (db session, redis, odoo client...)
│   │   ├── middleware.py           # Request logging, error handling, rate limit
│   │   └── routers/
│   │       ├── webhooks.py         # POST /webhook/{platform}
│   │       ├── orders.py           # Admin order management
│   │       ├── stock.py            # Stock sync endpoints
│   │       ├── reconciliation.py   # Reconciliation history & triggers
│   │       └── health.py           # GET /health
│   │
│   ├── connectors/                 # Platform-specific API clients
│   │   ├── base.py                 # BaseConnector abstract class
│   │   ├── shopee/
│   │   │   ├── __init__.py
│   │   │   ├── client.py           # ShopeeConnector
│   │   │   ├── auth.py             # Token management
│   │   │   ├── signing.py          # HMAC signing
│   │   │   └── exceptions.py       # Shopee-specific errors
│   │   ├── lazada/                 # Phase 2
│   │   └── tiktok/                 # Phase 2
│   │
│   ├── transformers/               # Data normalization
│   │   ├── base.py                 # BaseTransformer
│   │   ├── shopee.py               # ShopeeTransformer
│   │   ├── lazada.py               # Phase 2
│   │   └── tiktok.py               # Phase 2
│   │
│   ├── workers/                    # Celery tasks
│   │   ├── __init__.py
│   │   ├── app.py                  # Celery app instance
│   │   ├── order_worker.py         # sync_order_to_odoo, update_order_status
│   │   ├── stock_worker.py         # sync_stock_to_platform
│   │   ├── shipment_worker.py      # confirm_shipment
│   │   ├── price_worker.py         # sync_price_odoo_to_platform
│   │   └── scheduled.py            # Celery Beat: outbox relay, reconciliation,
│   │                               #   token refresh, stock safety net, cleanup
│   │
│   ├── odoo/                       # Odoo integration
│   │   ├── client.py               # OdooClient (XML-RPC)
│   │   ├── rest_client.py          # OdooRESTClient (REST API)
│   │   ├── exceptions.py           # Odoo-specific errors
│   │   └── field_mapping.py        # Field mapping constants
│   │
│   ├── models/                     # SQLAlchemy DB models
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── order_mapping.py
│   │   ├── webhook_outbox.py       # Transactional outbox
│   │   ├── product_mapping.py      # Gồm cả bundle_components
│   │   ├── stock_config.py
│   │   ├── platform_config.py      # Có dry_run + price_master fields
│   │   ├── reconciliation_log.py   # Nightly reconciliation results
│   │   └── price_sync_log.py       # Price sync history
│   │
│   ├── schemas/                    # Pydantic schemas
│   │   ├── unified.py              # UnifiedOrder, UnifiedProduct...
│   │   ├── api.py                  # Request/Response schemas
│   │   └── shopee.py               # Shopee raw webhook schemas
│   │
│   ├── services/                   # Business logic
│   │   ├── order_service.py        # Orchestrate order sync
│   │   ├── stock_service.py        # Stock allocation & sync
│   │   ├── outbox_service.py       # Outbox relay logic
│   │   ├── reconciliation_service.py # Nightly diff Shopee ↔ Odoo
│   │   ├── price_sync_service.py   # Odoo → Platform price sync
│   │   ├── token_service.py        # Token refresh management
│   │   ├── alert_service.py        # Slack/email alerts
│   │   └── mapping_service.py      # Product/order mapping CRUD
│   │
│   ├── core/                       # Shared utilities
│   │   ├── config.py               # Settings (Pydantic BaseSettings)
│   │   ├── database.py             # DB session, engine (primary + replica)
│   │   ├── redis.py                # Redis connection (AOF+RDB config)
│   │   ├── logging.py              # Structured logging setup
│   │   ├── exceptions.py           # Base exceptions
│   │   ├── rate_limit.py           # slowapi limiter setup
│   │   └── utils.py                # phone normalize, etc.
│   │
│   └── monitoring/
│       ├── metrics.py              # Prometheus metrics
│       └── health.py               # Health check (DB, Redis, Odoo, replica lag)
│
├── migrations/                     # Alembic migrations
│   ├── env.py
│   ├── script.py.mako
│   └── versions/
│       ├── 001_initial_tables.py
│       ├── 002_add_outbox.py
│       ├── 003_add_reconciliation.py
│       └── 004_add_price_sync.py
│
├── tests/
│   ├── unit/
│   │   ├── test_transformers/
│   │   ├── test_connectors/
│   │   ├── test_services/
│   │   └── test_reconciliation/
│   ├── integration/
│   │   ├── test_odoo_client.py
│   │   ├── test_shopee_webhook.py
│   │   └── test_outbox_relay.py
│   └── conftest.py
│
├── docker/
│   ├── Dockerfile
│   ├── Dockerfile.worker
│   ├── nginx.conf
│   ├── prometheus.yml
│   └── grafana/dashboards/
│
├── docs/
│   ├── context/                    # AI context files (bạn đang ở đây)
│   └── implementation/             # Outbox, Admin UI implementation guides
│
├── scripts/
│   ├── setup_odoo_fields.py        # Tạo custom fields trên Odoo
│   ├── seed_product_mapping.py     # Import product mapping từ CSV
│   ├── backfill_orders.py          # Re-sync orders bị miss
│   ├── shopee_gen_auth_url.py      # Tạo OAuth URL (dùng trong RB-001)
│   └── shopee_exchange_token.py    # Exchange auth code → tokens (RB-001)
│
├── .env.example
├── docker-compose.yml
├── docker-compose.prod.yml
├── requirements.txt
├── requirements-dev.txt
└── README.md
```

---

## Coding Conventions

### Naming

```python
# Files:       snake_case.py
# Classes:     PascalCase
# Functions:   snake_case
# Constants:   UPPER_SNAKE_CASE
# Variables:   snake_case
# Private:     _single_underscore
# DB columns:  snake_case

# ✅ Good
class ShopeeConnector:
    MAX_RETRY = 5

    async def get_order_detail(self, order_sn: str) -> dict:
        ...

# ❌ Bad
class shopee_connector:
    maxRetry = 5
    async def GetOrderDetail(self, orderSn):
        ...
```

### Async vs Sync

```python
# FastAPI endpoints & Connectors: ASYNC
# Celery tasks: SYNC (Celery không native async)
# Odoo XML-RPC: SYNC (thư viện không async)
# SQLAlchemy: dùng async session cho FastAPI, sync session cho Celery

# Pattern cho Celery gọi async code:
import asyncio

@app.task
def sync_order(order_data: dict):
    loop = asyncio.new_event_loop()
    result = loop.run_until_complete(_async_sync_order(order_data))
    loop.close()
    return result
```

### Error Handling

```python
# Hierarchy
class MiddlewareError(Exception): pass

class PlatformError(MiddlewareError):
    def __init__(self, platform: str, message: str, retryable: bool = True):
        self.platform  = platform
        self.retryable = retryable
        super().__init__(message)

class ShopeeError(PlatformError): pass
class ShopeeRateLimitError(ShopeeError):
    def __init__(self, retry_after: int = 60):
        self.retry_after = retry_after
        super().__init__("shopee", f"Rate limited, retry after {retry_after}s")

class OdooError(MiddlewareError): pass
class OdooConnectionError(OdooError): pass
class OdooValidationError(OdooError):
    def __init__(self, message: str):
        super().__init__(message)
        self.retryable = False  # Validation errors không retry được

# Pattern trong tasks
@app.task(bind=True)
def sync_order(self, order_data):
    try:
        ...
    except ShopeeRateLimitError as e:
        raise self.retry(countdown=e.retry_after)
    except OdooValidationError as e:
        # Không retry, log để xử lý thủ công
        log_dead_letter(order_data, str(e))
        return {"status": "dead_letter", "reason": str(e)}
    except MiddlewareError as e:
        if e.retryable:
            raise self.retry(countdown=60, exc=e)
        raise
```

### Logging

```python
# Dùng structlog - output JSON cho production
import structlog

logger = structlog.get_logger(__name__)

# Luôn include context
logger.info("order_synced",
    platform=order.platform,
    platform_order_id=order.platform_order_id,
    odoo_order_id=odoo_id,
    duration_ms=elapsed_ms
)

logger.error("sync_failed",
    platform=order.platform,
    platform_order_id=order.platform_order_id,
    error=str(e),
    retry_count=self.request.retries
)
```

### Configuration

```python
# src/core/config.py
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # Database
    DATABASE_URL:   str
    REDIS_URL:      str = "redis://localhost:6379/0"

    # Odoo
    ODOO_URL:       str
    ODOO_DB:        str
    ODOO_USER:      str
    ODOO_PASSWORD:  str

    # Shopee
    SHOPEE_PARTNER_ID:   str
    SHOPEE_PARTNER_KEY:  str
    SHOPEE_SHOP_ID:      str

    # App
    LOG_LEVEL:      str = "INFO"
    ENVIRONMENT:    str = "production"  # development/staging/production

    class Config:
        env_file = ".env"

settings = Settings()
```

---

## Testing Conventions

```python
# Unit tests: test_{module_name}.py
# Integration tests: test_{integration_name}_integration.py

# Fixtures trong conftest.py
# Mock external APIs (Shopee, Odoo) trong unit tests
# Dùng real connections trong integration tests (staging env)

# Ví dụ:
def test_shopee_transformer_maps_status_correctly():
    raw = {"order_status": "READY_TO_SHIP", ...}
    result = ShopeeTransformer().transform(raw)
    assert result.status == OrderStatus.CONFIRMED

def test_duplicate_order_skipped(db_session, mock_odoo):
    # Tạo existing mapping
    db_session.add(OrderMapping(platform="shopee", platform_order_id="TEST123"))

    # Chạy sync
    result = sync_order_to_odoo.apply(args=[sample_order])

    # Phải skip
    assert result.result["status"] == "skipped"
    mock_odoo.create_sale_order.assert_not_called()
```
