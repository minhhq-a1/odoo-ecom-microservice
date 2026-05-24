# Odoo E-Commerce Middleware

Self-hosted middleware Odoo 18 ↔ Shopee / Lazada / TikTok. Xử lý ~3.000 đơn/ngày.

## Tech stack
FastAPI 0.115 · Celery 5.4 · Redis 7 · PostgreSQL 16 · SQLAlchemy 2 · Pydantic 2 · httpx · PgBouncer.

## Quickstart

```bash
cp .env.example .env
# Edit .env: fill ODOO_*, SHOPEE_*, SECRET_KEY, ADMIN_SECRET_TOKEN
# Generate CREDENTIAL_KEYS:
python scripts/generate_fernet_key.py

make install
docker compose up -d redis postgres pgbouncer
make migrate
docker compose up -d
make smoke
```

Open:
- API:    http://localhost:8000/docs
- Admin:  http://localhost:8000/admin/?token=$ADMIN_SECRET_TOKEN
- Flower: http://localhost:5555
- Metrics: http://localhost:8000/metrics

## Documentation

| File | Topic |
|---|---|
| `CLAUDE.md` | Entry point cho AI assistant |
| `docs/01_PROJECT_OVERVIEW.md` | Mục tiêu, scope, go-live strategy |
| `docs/02_ARCHITECTURE.md` | System design, queue routing, HA |
| `docs/03_DATA_MODELS.md` | Schema + DB models |
| `docs/04_SHOPEE_API.md` | Shopee API reference |
| `docs/05_ODOO_INTEGRATION.md` | XML-RPC client + custom fields |
| `docs/06_PROJECT_STRUCTURE.md` | Layout, conventions, patterns |
| `docs/07_BUSINESS_RULES.md` | Idempotency, stock, dry-run, reconciliation |
| `docs/08_ENVIRONMENT_DEPLOYMENT.md` | Docker, env, load test |
| `docs/09_TESTING.md` | Test strategy + fixtures |
| `docs/10_SECURITY.md` | Threat model + hardening |
| `docs/11_OBSERVABILITY.md` | Metrics, SLO, alerts |
| `docs/12_CICD.md` | Pipeline + rollback |
| `docs/13_GAP_PATCHES.md` | v2.1 gap fixes |
| `docs/OUTBOX_PATTERN.md` | Outbox implementation guide |
| `docs/ADMIN_UI.md` | Admin UI templates + routes |

## Make targets

```
make help          List all targets
make install       Install runtime + dev deps
make lint          ruff check
make type          mypy --strict
make test-unit     pytest unit + coverage
make run           uvicorn dev server
make worker        celery worker (normal queue)
make beat          celery beat
make migrate       alembic upgrade head
make security      bandit + pip-audit
```

## Project state

Xem `CLAUDE.md` section "Trạng thái dự án hiện tại".

Phase 1 (Shopee) — docs + scaffold ready. Cần wire stock_safety_net, Shopee update_stock product lookup, ReconciliationService logic, load test trên staging trước go-live.
