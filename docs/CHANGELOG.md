# CHANGELOG — Context Updates

---

## v2.1 — 2026-05 (Current)

### Final implementation (post-gap-patch)

- ✅ Load test **executed**: 4/4 scenarios pass on local-staging stack (postgres:16 + redis:7 + uvicorn dry-run). 8,159 webhooks, 0 silent data loss, 0 dead-letter. Full report in `results/LOAD_TEST_REPORT.md`.
- ✅ Migration 005 fix — `current_database()` not valid in raw `GRANT` (wrapped in `DO $$ ... format(%I) ... $$`).
- ✅ `src/services/mapping_service.py` — Redis-cached ProductMapping lookup + reverse component → bundle search.
- ✅ `src/workers/stock_worker.py` — `stock_safety_net` iterates ProductMapping per platform, queues per-SKU sync; `sync_stock_for_sku` handles simple + bundle, calls `ShopeeConnector.update_stock`.
- ✅ `src/connectors/shopee/client.py:update_stock` — wired via ProductMapping (item_id from `platform_product_id`, model_id from `platform_sku_id` suffix), gracefully skips on flash sale lock.
- ✅ `src/services/reconciliation_service.py` — full implementation: fetch platform orders, diff vs synced mappings, auto-fix safe statuses (CONFIRMED/PROCESSING), field drift check (amount_total + tracking_number), persist + alert.
- ✅ `tests/load/` — 4 locustfiles (normal, flashsale, odoo_down, redis_restart) + signed payload helper.
- ✅ `scripts/verify_load_test.py` — Prometheus pass-criteria verifier (error rate, P95, dead_letter, outbox lag).
- ✅ `scripts/run_load_tests.sh` — orchestrates 4 scenarios end-to-end.
- ✅ `tests/unit/test_reconciliation_service.py` — auto-fix safe statuses, dry-run skip.

### Gaps closed (từ code review v2.0)


**Doc bổ sung:**
- `09_TESTING.md` — unit/integration/contract/load/chaos strategy, fixtures, coverage gate 85%.
- `10_SECURITY.md` — STRIDE threat model, Fernet envelope encryption + rotation, OWASP Top 10 checklist, audit log schema, Postgres/Redis hardening.
- `11_OBSERVABILITY.md` — SLO/SLI budget, Prometheus metrics catalog, Grafana dashboard list, AlertManager rules.
- `12_CICD.md` — GitHub Actions pipeline, blue-green deploy, migration safety check, rollback runbook.
- `13_GAP_PATCHES.md` — webhook_event_log model, AuditLog model, CircuitBreakerState, composite indexes, bundle stock logic, partner merge safeguard, worker concurrency tuning, inline token refresh fallback.

**Code scaffold (production-ready):**
- `src/core/`: config, logging, exceptions, database, redis, crypto, circuit_breaker, rate_limit, utils.
- `src/models/`: 11 SQLAlchemy 2.x models — order_mapping, outbox, webhook_event_log, order_sync_log, product_mapping + bundle_components, stock_config, platform_config, reconciliation_log, price_sync_log, audit_log.
- `src/schemas/unified.py`: Platform-agnostic dataclasses + status state machine.
- `src/connectors/shopee/`: client, signing (HMAC-SHA256), auth (token refresh w/ Redis lock).
- `src/transformers/shopee.py`: raw payload → UnifiedOrder.
- `src/odoo/client.py`: XML-RPC client + breaker + partner fuzzy match + dry-run gate.
- `src/services/`: outbox_service (skip_locked relay), order_service (idempotent sync), stock_service (buffer+allocation+bundle), alert_service.
- `src/workers/`: Celery app + beat schedule + order/stock/shipment/price workers + scheduled tasks.
- `src/api/`: FastAPI main, middleware (trace + body limit), webhooks (replay protection), health/ready/metrics, admin (audit-logged actions).
- `src/monitoring/`: Prometheus metrics catalog, health checks.

**Infra:**
- `migrations/`: Alembic env + 5 migrations (001 initial, 002 outbox, 003 reconciliation, 004 audit, 005 db_roles).
- `docker/Dockerfile` + `docker/Dockerfile.worker` — multi-stage, non-root user.
- `docker-compose.yml` — api, 3 workers, beat, redis (AOF+RDB), postgres, pgbouncer, flower.
- `pyproject.toml` — ruff strict, mypy strict, pytest config, coverage.
- `Makefile` — install/lint/type/test/run/migrate/security shortcuts.
- `.github/workflows/ci.yml` — lint + test + bandit + pip-audit + docker build + trivy.
- `requirements.txt` + `requirements-dev.txt` — pinned deps.
- `scripts/`: generate_fernet_key, setup_odoo_fields, check_migration_safety.

**Tests:**
- `tests/conftest.py` — pytest fixtures (fakeredis, env defaults, payload fixtures).
- `tests/unit/`: shopee transformer (param status mapping), HMAC signing, phone normalize, status machine, circuit breaker (open/half-open/close transitions).

### Behavior changes
- Status machine: `should_update_status(current, new)` chuẩn hóa transition rule, CANCELLED chỉ chấp nhận từ PENDING/CONFIRMED.
- Partner match: fuzzy ratio > 80 thay vì exact phone — chống gộp nhầm khách dùng số tổng đài.
- Webhook replay protection: Redis SETNX nonce TTL 5 phút.
- Token refresh: thêm Redis lock 60s — multi-worker safe.
- Outbox: `with_for_update(skip_locked=True)` — 2 relay worker không xử lý trùng entry.

---

## v2.0 — 2025-01

Xem `CHANGELOG.md` (original) phía dưới.

---

## v1.0 — 2025-01 (Initial)

- 01_PROJECT_OVERVIEW.md … 08_ENVIRONMENT_DEPLOYMENT.md
- CLAUDE.md
