# TESTING STRATEGY
## Unit · Integration · Load · Chaos · Contract Tests

---

## Mục tiêu

| Loại test | Coverage target | Tốc độ | Khi nào chạy |
|---|---|---|---|
| Unit | ≥ 85% line / ≥ 75% branch | < 30s | Mọi commit (pre-commit + CI) |
| Integration | Critical paths 100% | < 5 phút | CI on PR |
| Contract | All Shopee/Odoo endpoints | < 2 phút | CI + nightly |
| Load | 4 scenarios (xem 08) | 30 phút | Trước mỗi release |
| Chaos | 6 scenarios | 1 giờ | Trước go-live + quarterly |

---

## Stack

```
pytest 8.x                  — test runner
pytest-asyncio              — async fixtures
pytest-cov                  — coverage
pytest-xdist                — parallel execution
factory-boy                 — model factories
faker                       — fake data (vn locale)
respx                       — mock httpx (Shopee API)
fakeredis                   — in-memory Redis
testcontainers              — real Postgres in tests
locust                      — load test
toxiproxy / pumba           — chaos test
schemathesis                — OpenAPI contract test
ruff + mypy --strict        — lint/type gates
```

---

## Directory Layout

```
tests/
├── conftest.py                   # Root fixtures (db, redis, settings)
├── factories.py                  # factory-boy ModelFactory
├── fixtures/
│   ├── shopee_webhooks.py        # Sample payloads (order, logistics, stock)
│   ├── odoo_responses.py         # Mock XML-RPC responses
│   └── sample_orders.json        # Golden test data
├── unit/
│   ├── test_transformers/
│   │   └── test_shopee_transformer.py
│   ├── test_services/
│   │   ├── test_outbox_service.py
│   │   ├── test_stock_service.py
│   │   ├── test_reconciliation_service.py
│   │   └── test_price_sync_service.py
│   ├── test_connectors/
│   │   ├── test_shopee_signing.py
│   │   ├── test_shopee_auth.py
│   │   └── test_shopee_client.py
│   ├── test_workers/
│   │   ├── test_order_worker.py
│   │   └── test_stock_worker.py
│   ├── test_core/
│   │   ├── test_phone_normalize.py
│   │   ├── test_status_machine.py
│   │   └── test_circuit_breaker.py
│   └── test_models/
│       └── test_outbox.py
├── integration/
│   ├── test_webhook_to_outbox.py
│   ├── test_outbox_relay.py
│   ├── test_odoo_client.py
│   ├── test_shopee_webhook_e2e.py
│   ├── test_reconciliation_e2e.py
│   └── test_admin_api.py
├── contract/
│   ├── test_shopee_api_contract.py
│   └── test_odoo_xmlrpc_contract.py
├── load/
│   ├── locustfile_normal.py
│   ├── locustfile_flashsale.py
│   └── locustfile_odoo_down.py
└── chaos/
    ├── test_redis_restart.py
    ├── test_postgres_failover.py
    └── test_odoo_slow.py
```

---

## conftest.py (root)

```python
import asyncio
import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from fakeredis.aioredis import FakeRedis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from src.core.config import Settings, get_settings
from src.models.base import Base


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
def postgres_container():
    with PostgresContainer("postgres:16-alpine") as pg:
        yield pg


@pytest_asyncio.fixture(scope="session")
async def db_engine(postgres_container):
    url = postgres_container.get_connection_url().replace(
        "postgresql+psycopg2", "postgresql+asyncpg"
    )
    engine = create_async_engine(url, future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine) -> AsyncIterator[AsyncSession]:
    Session = async_sessionmaker(db_engine, expire_on_commit=False)
    async with Session() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture
async def redis():
    r = FakeRedis(decode_responses=True)
    yield r
    await r.flushall()


@pytest.fixture
def settings(monkeypatch) -> Settings:
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("MIDDLEWARE_DRY_RUN", "true")
    monkeypatch.setenv("SHOPEE_PARTNER_KEY", "test_partner_key")
    get_settings.cache_clear()
    return get_settings()


@pytest.fixture
def mock_odoo(mocker):
    """Mock OdooClient toàn bộ method common."""
    client = mocker.MagicMock()
    client.check_order_exists.return_value = None
    client.get_or_create_partner.return_value = 42
    client.create_sale_order.return_value = (100, "SO0100")
    client.get_stock_quantity.return_value = 50
    return client


@pytest.fixture
def shopee_order_payload():
    return {
        "code": 3,
        "shop_id": 123456,
        "timestamp": 1700000000,
        "data": {"ordersn": "230101ABCDEF01", "status": "READY_TO_SHIP",
                 "update_time": 1700000000},
    }
```

---

## Factories (factories.py)

```python
import factory
from factory.alchemy import SQLAlchemyModelFactory

from src.models.order_mapping import OrderMapping
from src.models.outbox import WebhookOutbox


class OrderMappingFactory(SQLAlchemyModelFactory):
    class Meta:
        model = OrderMapping
        sqlalchemy_session_persistence = "commit"

    platform = "shopee"
    platform_order_id = factory.Sequence(lambda n: f"SHOPEE{n:08d}")
    status = "pending"
    retry_count = 0


class WebhookOutboxFactory(SQLAlchemyModelFactory):
    class Meta:
        model = WebhookOutbox

    platform = "shopee"
    event_type = "order"
    event_code = 3
    payload = factory.LazyFunction(lambda: {"code": 3, "data": {}})
    status = "pending"
```

---

## Test Patterns

### Unit — Transformer (pure function)

```python
def test_shopee_transformer_maps_ready_to_ship_to_confirmed():
    raw = {"order_status": "READY_TO_SHIP", "ordersn": "X", "item_list": []}
    result = ShopeeTransformer().transform(raw)
    assert result.status == OrderStatus.CONFIRMED

@pytest.mark.parametrize("shopee_status,expected", [
    ("UNPAID", OrderStatus.PENDING),
    ("READY_TO_SHIP", OrderStatus.CONFIRMED),
    ("SHIPPED", OrderStatus.SHIPPED),
    ("CANCELLED", OrderStatus.CANCELLED),
])
def test_status_mapping(shopee_status, expected):
    assert SHOPEE_STATUS_MAP[shopee_status] == expected
```

### Unit — Idempotency

```python
async def test_duplicate_order_skipped(db_session, mock_odoo):
    OrderMappingFactory(platform="shopee", platform_order_id="DUP1", status="success")
    await db_session.commit()

    result = await OrderService(db_session, mock_odoo).sync(
        platform="shopee", platform_order_id="DUP1"
    )
    assert result.status == "skipped"
    mock_odoo.create_sale_order.assert_not_called()
```

### Integration — Webhook → Outbox

```python
async def test_shopee_webhook_persists_to_outbox(
    async_client, db_session, shopee_order_payload, sign_shopee_body
):
    body = json.dumps(shopee_order_payload).encode()
    sig = sign_shopee_body(body)
    resp = await async_client.post("/webhook/shopee", content=body,
                                    headers={"X-Shopee-Signature": sig})
    assert resp.status_code == 200

    entry = (await db_session.execute(
        select(WebhookOutbox).where(WebhookOutbox.platform_order_id == "230101ABCDEF01")
    )).scalar_one()
    assert entry.status == "pending"
    assert entry.event_code == 3
```

### Integration — Outbox Relay Race

```python
async def test_relay_uses_skip_locked(db_session):
    """2 relay workers chạy đồng thời không xử lý cùng entry."""
    for i in range(10):
        WebhookOutboxFactory(status="pending")
    await db_session.commit()

    results = await asyncio.gather(
        OutboxService.relay_pending(batch_size=10),
        OutboxService.relay_pending(batch_size=10),
    )
    total = results[0]["published"] + results[1]["published"]
    assert total == 10  # Không xử lý trùng
```

### Contract — Shopee API

```python
# schemathesis cho REST endpoints
# Manual cho XML-RPC

@pytest.mark.contract
async def test_shopee_get_order_detail_schema(respx_mock):
    respx_mock.get(SHOPEE_BASE + "/order/get_order_detail").respond(
        json=load_fixture("shopee/get_order_detail_success.json")
    )
    result = await connector.get_order_detail(["SN1"])
    # Validate response shape match unified schema
    assert isinstance(result[0], UnifiedOrder)
```

---

## Chaos Scenarios

| ID | Scenario | Expected |
|---|---|---|
| CH-01 | Redis kill -9 trong khi relay đang chạy | Relay sau khi Redis lên xử lý tiếp, 0 entry mất |
| CH-02 | Postgres failover sang replica | API trả 503 đến khi promote done, không corrupt data |
| CH-03 | Odoo response 30s timeout | Circuit breaker mở, dead_letter sau max_retries |
| CH-04 | Network partition middleware ↔ Shopee 10 phút | Polling backfill khi kết nối lại |
| CH-05 | Disk full PostgreSQL | API trả 503, không silently drop webhook |
| CH-06 | Celery worker OOM kill | Task ack_late=True đảm bảo re-deliver |

---

## CI Gates (block merge)

```yaml
# .github/workflows/ci.yml gates:
1. ruff check --select=ALL --exit-non-zero-on-fix
2. mypy --strict src/
3. pytest tests/unit  -n auto --cov=src --cov-fail-under=85
4. pytest tests/integration
5. pip-audit  (CVE scan)
6. bandit -r src/
7. detect-secrets scan
```

---

## Coverage Exclusions

```ini
# pyproject.toml
[tool.coverage.run]
omit = [
    "src/migrations/*",
    "src/scripts/*",
    "*/conftest.py",
]
[tool.coverage.report]
exclude_lines = [
    "pragma: no cover",
    "raise NotImplementedError",
    "if __name__ == .__main__.:",
    "@abstractmethod",
]
```

---

## Test Data Management

- **Sandbox Shopee** dùng riêng cho integration test, partner_key trong GitHub Secrets.
- **Odoo test DB** — Docker image với data snapshot pre-loaded (`odoo-test-data:17.0`).
- **Reset giữa các test** — db_session rollback + redis flushall.
- **Không commit credentials** — chỉ dùng `.env.test` template, secrets inject CI runtime.
