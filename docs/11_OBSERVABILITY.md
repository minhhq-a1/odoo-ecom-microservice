# OBSERVABILITY
## Metrics · Logs · Traces · SLO · Alerts

---

## SLOs (Service Level Objectives)

| Service | Indicator | Target | Window | Error Budget |
|---|---|---|---|---|
| Webhook ingest | HTTP 2xx rate | 99.9% | 30d rolling | 43.2 phút/tháng |
| Webhook ingest | P95 response time | < 500ms | 30d | — |
| Order sync | E2E latency (webhook → Odoo) P95 | < 5 phút | 30d | — |
| Order sync | Success rate (sync attempted) | 99.5% | 30d | 3.6h/tháng |
| Stock sync | Lag từ Odoo update → sàn P95 | < 3 phút | 30d | — |
| Reconciliation | Đơn miss được auto-fix | ≥ 95% | mỗi run | — |
| Outbox relay | Tồn pending > 100 entries | 0 events | 30d | — |

### Burn rate alerts

```
Fast burn:   2% budget trong 1h → page (PagerDuty)
Slow burn:   5% budget trong 6h → ticket
```

---

## Metrics Catalog (Prometheus)

### Naming convention
```
mw_<subsystem>_<metric>_<unit>
- subsystem: webhook | outbox | order | stock | odoo | shopee | reconciliation
- unit: total (counter) | seconds (histogram) | ratio | count (gauge)
```

### Webhook layer

```python
mw_webhook_received_total{platform, event_type}                  # Counter
mw_webhook_signature_invalid_total{platform}                     # Counter
mw_webhook_response_seconds{platform, status_code}               # Histogram
mw_webhook_body_size_bytes{platform}                             # Histogram
```

### Outbox

```python
mw_outbox_pending_count{platform, event_type}                    # Gauge
mw_outbox_published_total{platform, event_type}                  # Counter
mw_outbox_dead_letter_total{platform, event_type}                # Counter
mw_outbox_relay_duration_seconds                                 # Histogram
mw_outbox_oldest_pending_age_seconds                             # Gauge — KEY
```

### Order sync

```python
mw_order_sync_attempts_total{platform, result}                   # Counter (result: success|failed|skipped|dead_letter)
mw_order_sync_duration_seconds{platform}                         # Histogram
mw_order_sync_e2e_latency_seconds{platform}                      # Histogram (webhook → Odoo done)
mw_order_status_transition_total{platform, from, to}             # Counter
```

### Stock sync

```python
mw_stock_sync_total{platform, sku, result}                       # Counter
mw_stock_sync_lag_seconds{platform}                              # Histogram
mw_stock_calculation_negative_prevented_total{sku}               # Counter (audit)
```

### Odoo client

```python
mw_odoo_request_total{method, result}                            # Counter
mw_odoo_request_duration_seconds{method}                         # Histogram
mw_odoo_circuit_breaker_state{state}                             # Gauge (0=closed,1=open,2=halfopen)
mw_odoo_circuit_breaker_open_total                               # Counter
```

### Shopee client

```python
mw_shopee_request_total{endpoint, status}                        # Counter
mw_shopee_rate_limit_hits_total{endpoint}                        # Counter
mw_shopee_token_age_seconds{shop_id, token_type}                 # Gauge (alert > 3.5h)
mw_shopee_refresh_token_expires_in_seconds{shop_id}              # Gauge (alert < 7d)
```

### Infrastructure

```python
mw_redis_command_duration_seconds{command}                       # Histogram
mw_postgres_replica_lag_seconds                                  # Gauge
mw_celery_queue_depth{queue}                                     # Gauge
mw_celery_task_duration_seconds{task_name, result}               # Histogram
mw_celery_worker_active                                          # Gauge
```

---

## Metrics middleware (FastAPI)

```python
# src/monitoring/metrics.py
from prometheus_client import Counter, Histogram, Gauge, generate_latest

WEBHOOK_RECEIVED = Counter("mw_webhook_received_total", "", ["platform", "event_type"])
WEBHOOK_DURATION = Histogram("mw_webhook_response_seconds", "",
                              ["platform", "status_code"],
                              buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5))
ODOO_DURATION = Histogram("mw_odoo_request_duration_seconds", "",
                          ["method"],
                          buckets=(0.1, 0.5, 1, 2, 5, 10, 30))
CB_STATE = Gauge("mw_odoo_circuit_breaker_state", "", ["service"])

@app.get("/metrics")
async def metrics():
    return Response(generate_latest(), media_type="text/plain; version=0.0.4")
```

---

## Structured Logging

```python
# Mọi log entry phải có:
trace_id        — uuid4 per request, propagate qua queue (Celery header)
span_id         — uuid4 per operation
platform        — shopee | lazada | tiktok | odoo
event           — snake_case event name
duration_ms     — nếu là operation kết thúc

# Bắt buộc cho order-related:
platform_order_id
odoo_order_id (nếu có)

# structlog config
structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.dict_tracebacks,
        structlog.processors.JSONRenderer(),
    ]
)

# Middleware inject trace_id
@app.middleware("http")
async def trace_middleware(request, call_next):
    trace_id = request.headers.get("X-Trace-Id") or str(uuid4())
    structlog.contextvars.bind_contextvars(trace_id=trace_id)
    response = await call_next(request)
    response.headers["X-Trace-Id"] = trace_id
    return response
```

### Log levels

```
DEBUG     — chi tiết XML-RPC params, payload đầy đủ (KHÔNG bật prod)
INFO      — event chính (order_synced, stock_updated, token_refreshed)
WARNING   — retry, rate limit hit, circuit breaker open, token expire soon
ERROR     — exception không expected, dead_letter
CRITICAL  — system down (DB unreachable, Redis OOM)
```

### Redaction

```python
SENSITIVE_KEYS = {"password", "access_token", "refresh_token", "partner_key",
                  "credentials", "x-shopee-signature"}

def redact_processor(logger, method, event_dict):
    for k in list(event_dict.keys()):
        if k.lower() in SENSITIVE_KEYS:
            event_dict[k] = "***REDACTED***"
    return event_dict
```

---

## Distributed Tracing (OpenTelemetry — Phase 2)

```python
# Phase 1: trace_id qua structlog đủ dùng
# Phase 2 khi multi-service: OpenTelemetry → Tempo / Jaeger

from opentelemetry import trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.instrumentation.celery import CeleryInstrumentor

FastAPIInstrumentor.instrument_app(app)
SQLAlchemyInstrumentor().instrument(engine=engine)
CeleryInstrumentor().instrument()
```

Trace propagation qua Celery: dùng `headers={"traceparent": ...}` trong `apply_async`.

---

## Grafana Dashboards

```
1. Overview                  — SLO compliance, error budget burn, top errors
2. Webhook Health            — RPS, P95/99, signature failures, body size
3. Outbox Health             — Pending count by age bucket, dead_letter rate, relay duration
4. Order Pipeline            — E2E latency funnel, status transitions, by platform
5. Stock Sync                — Lag heatmap, sync rate, flash sale lock incidents
6. Odoo Client               — RPS, latency, circuit state, top slow methods
7. Shopee Client             — RPS, 429 rate, token age countdown
8. Infrastructure            — Postgres connections, replica lag, Redis memory, Celery queue depth
9. Business KPI              — đơn/giờ, GMV, reconciliation miss rate, manual review count
```

Dashboards JSON commit vào `docker/grafana/dashboards/`.

---

## Alerting Rules (Prometheus AlertManager)

```yaml
# docker/prometheus/alerts.yml
groups:
- name: middleware-critical
  rules:
  - alert: WebhookErrorRateHigh
    expr: |
      sum(rate(mw_webhook_response_seconds_count{status_code=~"5.."}[5m]))
      / sum(rate(mw_webhook_response_seconds_count[5m])) > 0.01
    for: 5m
    labels: { severity: critical, page: "true" }
    annotations:
      summary: "Webhook 5xx > 1% trong 5 phút"

  - alert: OutboxOldestPendingHigh
    expr: mw_outbox_oldest_pending_age_seconds > 300
    for: 2m
    labels: { severity: critical, page: "true" }
    annotations:
      summary: "Outbox có entry pending > 5 phút — relay nghẽn"

  - alert: OutboxDeadLetter
    expr: increase(mw_outbox_dead_letter_total[5m]) > 0
    labels: { severity: critical, page: "true" }

  - alert: OdooCircuitOpen
    expr: mw_odoo_circuit_breaker_state == 1
    for: 1m
    labels: { severity: critical, page: "true" }

  - alert: ShopeeRefreshTokenExpiringSoon
    expr: mw_shopee_refresh_token_expires_in_seconds < 604800   # 7 ngày
    labels: { severity: warning }

  - alert: ShopeeAccessTokenAgeHigh
    expr: mw_shopee_token_age_seconds{token_type="access"} > 12600   # 3.5h
    labels: { severity: warning }

  - alert: PostgresReplicaLagHigh
    expr: mw_postgres_replica_lag_seconds > 30
    for: 5m
    labels: { severity: warning }

  - alert: CeleryQueueDepthHigh
    expr: mw_celery_queue_depth{queue="queue:orders.created.normal"} > 500
    for: 10m
    labels: { severity: warning }

  - alert: ReconciliationNeedsReview
    expr: increase(mw_reconciliation_needs_review_total[1d]) > 0
    labels: { severity: warning }

  - alert: RedisMemoryHigh
    expr: redis_memory_used_bytes / redis_memory_max_bytes > 0.8
    for: 10m
    labels: { severity: warning }
```

### Alert routing

```yaml
# alertmanager.yml
route:
  receiver: slack-default
  group_by: [alertname, severity]
  routes:
    - matchers: [severity="critical", page="true"]
      receiver: pagerduty
    - matchers: [severity="warning"]
      receiver: slack-ops

receivers:
  - name: pagerduty
    pagerduty_configs:
      - service_key: ${PD_KEY}
  - name: slack-ops
    slack_configs:
      - api_url: ${SLACK_WEBHOOK}
        channel: "#middleware-ops"
```

---

## Runtime checks

```python
# /health — liveness (kubelet probe)
@app.get("/health")
async def health():
    return {"status": "ok"}

# /ready — readiness (load balancer)
@app.get("/ready")
async def ready():
    checks = await asyncio.gather(
        check_postgres(), check_redis(), check_odoo_reachable(),
        return_exceptions=True,
    )
    ok = all(not isinstance(c, Exception) and c.ok for c in checks)
    return Response(
        json.dumps({"checks": [c.dict() for c in checks]}),
        status_code=200 if ok else 503,
    )

# /metrics — Prometheus scrape
```

---

## Cost Observability

```
Track:
  - Tokens API calls Shopee per shop (rate limit budget)
  - Odoo XML-RPC calls per hour
  - Postgres connections per service
  - Redis memory bytes
  - Celery task seconds per worker

Mục đích: cost attribution + capacity planning.
```
