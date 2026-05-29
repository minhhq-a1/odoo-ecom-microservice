# DEPLOYMENT GUIDE
## Production Infrastructure · Secrets Management · Blue-Green Deployment

**Last Updated:** 2026-05-29
**Audience:** DevOps Team, Infrastructure Engineers
**Scope:** Production deployment procedures for Odoo E-Commerce Middleware

---

## 🏗️ Infrastructure Requirements

### Minimum Production Setup

| Component | Specs | Quantity | Notes |
|-----------|-------|----------|-------|
| **API Server** | 4 vCPU, 8GB RAM | 2+ | Behind load balancer |
| **Celery Workers** | 4 vCPU, 8GB RAM | 3+ | Auto-scale based on queue depth |
| **PostgreSQL** | 8 vCPU, 16GB RAM, 500GB SSD | 1 Primary + 1 Replica | Streaming replication |
| **Redis** | 2 vCPU, 4GB RAM | 1 | Persistence enabled (AOF) |
| **PgBouncer** | 2 vCPU, 2GB RAM | 1 | Connection pooling |
| **Load Balancer** | — | 1 | NGINX or cloud LB |

### Network Topology

```
Internet
   │
   ▼
┌─────────────────────────────────────────────────────────────┐
│                    Load Balancer (HTTPS)                     │
│                  SSL Termination + Rate Limit                │
└────────────────────────┬────────────────────────────────────┘
                         │
        ┌────────────────┼────────────────┐
        ▼                ▼                ▼
   ┌─────────┐     ┌─────────┐     ┌─────────┐
   │ API #1  │     │ API #2  │     │ API #3  │
   │ :8000   │     │ :8000   │     │ :8000   │
   └────┬────┘     └────┬────┘     └────┬────┘
        │               │               │
        └───────────────┼───────────────┘
                        │
        ┌───────────────┼───────────────┐
        ▼               ▼               ▼
   ┌──────────┐   ┌──────────┐   ┌──────────┐
   │ Worker#1 │   │ Worker#2 │   │ Worker#3 │
   │ (high)   │   │ (default)│   │ (low)    │
   └────┬─────┘   └────┬─────┘   └────┬─────┘
        │              │              │
        └──────────────┼──────────────┘
                       │
        ┌──────────────┼──────────────┐
        ▼              ▼              ▼
   ┌─────────┐   ┌──────────┐   ┌─────────┐
   │  Redis  │   │ PgBouncer│   │ Postgres│
   │  :6379  │   │  :6432   │   │  :5432  │
   └─────────┘   └────┬─────┘   └────┬────┘
                      │              │
                      └──────────────┘
```

### Scaling Considerations

**Horizontal Scaling:**
- **API Servers:** Scale based on CPU (target 70%)
- **Workers:** Scale based on queue depth (`mw_celery_queue_length` > 100)
- **PostgreSQL:** Read replicas for reporting/reconciliation

**Vertical Scaling:**
- **Redis:** Increase memory if `used_memory_rss` > 80%
- **PostgreSQL:** Increase RAM for buffer cache (target 25% of DB size)

---

## 🐳 Docker Production Setup

### Multi-Stage Dockerfile (Optimized)

```dockerfile
# docker/Dockerfile.prod
FROM python:3.12-slim AS builder

WORKDIR /build
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --user --no-cache-dir -r requirements.txt

# ────────────────────────────────────────────────────────────────
FROM python:3.12-slim

# Non-root user
RUN useradd -m -u 1000 appuser

WORKDIR /app
COPY --from=builder /root/.local /home/appuser/.local
COPY src/ ./src/
COPY alembic.ini migrations/ ./

RUN chown -R appuser:appuser /app
USER appuser

ENV PATH=/home/appuser/.local/bin:$PATH
ENV PYTHONUNBUFFERED=1

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import httpx; httpx.get('http://localhost:8000/health/ready', timeout=3)"

EXPOSE 8000
CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
```

### docker-compose.prod.yml

```yaml
version: "3.9"

services:
  api:
    image: ${REGISTRY}/middleware-api:${VERSION}
    restart: unless-stopped
    ports:
      - "8000:8000"
    env_file: .env.prod
    depends_on:
      - redis
      - pgbouncer
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health/ready"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 10s
    deploy:
      replicas: 2
      resources:
        limits:
          cpus: '4'
          memory: 8G
        reservations:
          cpus: '2'
          memory: 4G
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"

  worker-high:
    image: ${REGISTRY}/middleware-worker:${VERSION}
    restart: unless-stopped
    env_file: .env.prod
    depends_on:
      - redis
      - pgbouncer
    command: >
      celery -A src.workers.app worker
      --loglevel=info
      --queues=orders.created.high,shipment.confirm
      --concurrency=4
      --max-tasks-per-child=1000
      --hostname=worker-high@%h
    deploy:
      replicas: 2
      resources:
        limits:
          cpus: '4'
          memory: 8G

  worker-default:
    image: ${REGISTRY}/middleware-worker:${VERSION}
    restart: unless-stopped
    env_file: .env.prod
    depends_on:
      - redis
      - pgbouncer
    command: >
      celery -A src.workers.app worker
      --loglevel=info
      --queues=orders.created.default,stock.sync,price.sync
      --concurrency=8
      --max-tasks-per-child=1000
      --hostname=worker-default@%h
    deploy:
      replicas: 3

  beat:
    image: ${REGISTRY}/middleware-worker:${VERSION}
    restart: unless-stopped
    env_file: .env.prod
    depends_on:
      - redis
      - pgbouncer
    command: celery -A src.workers.app beat --loglevel=info

  pgbouncer:
    image: edoburu/pgbouncer:1.21
    restart: unless-stopped
    environment:
      DATABASE_URL: ${DATABASE_URL}
      POOL_MODE: transaction
      MAX_CLIENT_CONN: 1000
      DEFAULT_POOL_SIZE: 25
      RESERVE_POOL_SIZE: 5
    ports:
      - "6432:5432"

  redis:
    image: redis:7-alpine
    restart: unless-stopped
    command: redis-server --appendonly yes --maxmemory 2gb --maxmemory-policy allkeys-lru
    volumes:
      - redis-data:/data
    ports:
      - "6379:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 3s
      retries: 3

volumes:
  redis-data:
```

---

## 🔐 Secrets Management

### AWS Secrets Manager Integration

**1. Store Secrets in AWS Secrets Manager:**

```bash
# Create secret
aws secretsmanager create-secret \
  --name prod/middleware/env \
  --description "Production environment variables" \
  --secret-string file://secrets.json

# secrets.json format:
{
  "SECRET_KEY": "your-secret-key-here",  # pragma: allowlist secret
  "ADMIN_SECRET_TOKEN": "your-admin-token",  # pragma: allowlist secret
  "DATABASE_URL": "postgresql+************************/db",  # pragma: allowlist secret
  "ODOO_PASSWORD": "your-odoo-api-key",  # pragma: allowlist secret
  "SHOPEE_PARTNER_KEY": "your-shopee-key",
  "SLACK_WEBHOOK_URL": "https://hooks.slack.com/services/xxx"
}
```

**2. Fetch Secrets at Runtime:**

```python
# src/core/secrets.py
import json
import boto3
from functools import lru_cache

@lru_cache(maxsize=1)
def get_secrets() -> dict:
    """Fetch secrets from AWS Secrets Manager."""
    client = boto3.client('secretsmanager', region_name='ap-southeast-1')
    response = client.get_secret_value(SecretId='prod/middleware/env')
    return json.loads(response['SecretString'])

# Usage in config.py
secrets = get_secrets()
SECRET_KEY = secrets['SECRET_KEY']
```

**3. IAM Role for ECS/EC2:**

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "secretsmanager:GetSecretValue",
        "secretsmanager:DescribeSecret"
      ],
      "Resource": "arn:aws:secretsmanager:**************:*:secret:prod/middleware/*"  # pragma: allowlist secret
    }
  ]
}
```

### Key Rotation Procedures

**Quarterly Rotation (Every 90 days):**

1. **Generate new keys:**
   ```bash
   # SECRET_KEY
   python -c "import secrets; print(secrets.token_urlsafe(32))"

   # ADMIN_SECRET_TOKEN
   python -c "import secrets; print(secrets.token_hex(32))"
   ```

2. **Update AWS Secrets Manager:**
   ```bash
   aws secretsmanager update-secret \
     --secret-id prod/middleware/env \
     --secret-string file://secrets-new.json
   ```

3. **Rolling restart (zero downtime):**
   ```bash
   # API servers (one by one)
   docker-compose -f docker-compose.prod.yml up -d --no-deps --scale api=1 api
   sleep 30  # Wait for health check
   docker-compose -f docker-compose.prod.yml up -d --no-deps --scale api=2 api

   # Workers (graceful shutdown)
   docker-compose -f docker-compose.prod.yml restart worker-high worker-default
   ```

4. **Verify rotation:**
   ```bash
   curl -H "Authorization: Bearer NEW_TOKEN" https://middleware.example.com/admin/
   ```

### Backup & Recovery

**Database Backup:**
```bash
# Automated daily backup (cron)
0 2 * * * pg_dump -h postgres -U middleware middleware_db | \
  gzip > /backups/middleware_$(date +\%Y\%m\%d).sql.gz && \
  aws s3 cp /backups/middleware_$(date +\%Y\%m\%d).sql.gz \
  s3://backups/middleware/

# Retention: 7 days local, 30 days S3
```

**Secrets Backup:**
```bash
# Export secrets (encrypted)
aws secretsmanager get-secret-value \
  --secret-id prod/middleware/env \
  --query SecretString --output text | \
  gpg --encrypt --recipient ops@company.com > secrets.gpg

# Store in secure location (1Password, S3 with KMS)
```

---

## 🔄 Blue-Green Deployment

### Overview

```
┌─────────────────────────────────────────────────────────────┐
│                      Load Balancer                           │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  Route 100% traffic to:                              │  │
│  │  [ ] Blue (current)   [✓] Green (new)               │  │
│  └──────────────────────────────────────────────────────┘  │
└────────────┬────────────────────────────┬───────────────────┘
             │                            │
    ┌────────▼────────┐          ┌────────▼────────┐
    │  Blue Stack     │          │  Green Stack    │
    │  (v1.2.3)       │          │  (v1.3.0)       │
    │                 │          │                 │
    │  API x2         │          │  API x2         │
    │  Worker x3      │          │  Worker x3      │
    └─────────────────┘          └─────────────────┘
             │                            │
             └────────────┬───────────────┘
                          │
                   ┌──────▼──────┐
                   │  Shared DB  │
                   │  & Redis    │
                   └─────────────┘
```

### Step-by-Step Procedure

**Pre-Deployment Checklist:**
- [ ] Database migrations tested in staging
- [ ] Rollback plan documented
- [ ] Monitoring dashboards ready
- [ ] On-call engineer available
- [ ] Business stakeholders notified

**1. Prepare Green Stack:**

```bash
# Set version
export VERSION=v1.3.0
export REGISTRY=your-registry.com

# Build and push images
docker build -f docker/Dockerfile.prod -t ${REGISTRY}/middleware-api:${VERSION} .
docker build -f docker/Dockerfile.worker -t ${REGISTRY}/middleware-worker:${VERSION} .
docker push ${REGISTRY}/middleware-api:${VERSION}
docker push ${REGISTRY}/middleware-worker:${VERSION}

# Deploy green stack (separate compose file)
docker-compose -f docker-compose.green.yml up -d
```

**2. Run Database Migrations:**

```bash
# Connect to green API container
docker exec -it green-api-1 bash

# Run migrations (idempotent)
alembic upgrade head

# Verify migration
alembic current
```

**3. Smoke Test Green Stack:**

```bash
# Health check
curl -f http://green-api:8000/health/ready

# Test webhook endpoint (dry-run)
curl -X POST http://green-api:8000/webhook/shopee \
  -H "Content-Type: application/json" \
  -d '{"test": true}'

# Check metrics
curl http://green-api:8000/metrics | grep mw_app_info
```

**4. Switch Traffic (10% → 50% → 100%):**

```bash
# NGINX config: /etc/nginx/conf.d/middleware.conf
upstream backend {
    server blue-api:8000 weight=90;   # Blue: 90%
    server green-api:8000 weight=10;  # Green: 10%
}

# Reload NGINX
nginx -s reload

# Monitor for 5 minutes
watch -n 5 'curl -s http://localhost/metrics | grep mw_http_requests_total'

# If OK, increase to 50%
# ... then 100%
upstream backend {
    server green-api:8000 weight=100;  # Green: 100%
}
```

**5. Post-Deployment Verification:**

```bash
# Check error rate (should be < 1%)
curl -s http://localhost/metrics | grep mw_http_requests_total | grep status=\"5

# Check queue depth (should be < 100)
curl -s http://localhost/metrics | grep mw_celery_queue_length

# Check circuit breaker (should be closed)
curl -s http://localhost/metrics | grep mw_circuit_breaker_state

# Verify recent orders synced
curl -H "Authorization: Bearer ${ADMIN_TOKEN}" \
  http://localhost/admin/orders?limit=10
```

**6. Decommission Blue Stack:**

```bash
# Wait 1 hour for confidence
sleep 3600

# Stop blue stack (keep for 24h rollback window)
docker-compose -f docker-compose.blue.yml stop

# After 24h, remove
docker-compose -f docker-compose.blue.yml down
```

### Rollback Procedure

**If issues detected within 1 hour:**

```bash
# 1. Switch traffic back to blue (immediate)
# NGINX config
upstream backend {
    server blue-api:8000 weight=100;  # Blue: 100%
    server green-api:8000 weight=0;   # Green: 0%
}
nginx -s reload

# 2. Stop green stack
docker-compose -f docker-compose.green.yml stop

# 3. Rollback database migration (if needed)
docker exec -it blue-api-1 bash
alembic downgrade -1

# 4. Alert team
curl -X POST ${SLACK_WEBHOOK_URL} \
  -H 'Content-Type: application/json' \
  -d '{"text": "🚨 Rollback executed: v1.3.0 → v1.2.3"}'
```

---

## 📊 Monitoring & Alerting

### Prometheus Metrics Setup

**1. Prometheus Configuration:**

```yaml
# prometheus.yml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

scrape_configs:
  - job_name: 'middleware-api'
    static_configs:
      - targets: ['api-1:8000', 'api-2:8000']
    metrics_path: '/metrics'

  - job_name: 'middleware-workers'
    static_configs:
      - targets: ['worker-high-1:9090', 'worker-default-1:9090']

  - job_name: 'postgres'
    static_configs:
      - targets: ['postgres-exporter:9187']

  - job_name: 'redis'
    static_configs:
      - targets: ['redis-exporter:9121']
```

**2. Grafana Dashboards:**

**Dashboard: Middleware Overview**
- **Panel 1:** Request rate (req/s)
- **Panel 2:** Error rate (%)
- **Panel 3:** P95 latency (ms)
- **Panel 4:** Queue depth (orders/stock/shipment)
- **Panel 5:** Circuit breaker state
- **Panel 6:** Outbox oldest pending age

**Dashboard: Business Metrics**
- **Panel 1:** Orders synced (last 24h)
- **Panel 2:** Sync success rate (%)
- **Panel 3:** Dead letter count
- **Panel 4:** Stock sync lag (minutes)

**Import JSON:**
```bash
# Download dashboard
curl -o grafana-dashboard.json \
  https://raw.githubusercontent.com/your-org/middleware/main/monitoring/grafana-dashboard.json

# Import via Grafana UI or API
curl -X POST http://grafana:3000/api/dashboards/db \
  -H "Authorization: Bearer ${GRAFANA_API_KEY}" \
  -H "Content-Type: application/json" \
  -d @grafana-dashboard.json
```

### Alert Rules

**alertmanager.yml:**

```yaml
route:
  receiver: 'slack-ops'
  group_by: ['alertname', 'severity']
  group_wait: 10s
  group_interval: 5m
  repeat_interval: 4h

receivers:
  - name: 'slack-ops'
    slack_configs:
      - api_url: '${SLACK_WEBHOOK_URL}'
        channel: '#ops-alerts'
        title: '{{ .GroupLabels.alertname }}'
        text: '{{ range .Alerts }}{{ .Annotations.description }}{{ end }}'

  - name: 'pagerduty'
    pagerduty_configs:
      - service_key: '${PAGERDUTY_KEY}'
        severity: '{{ .GroupLabels.severity }}'
```

**alert-rules.yml:**

```yaml
groups:
  - name: middleware
    interval: 30s
    rules:
      # High error rate
      - alert: HighErrorRate
        expr: |
          rate(mw_http_requests_total{status=~"5.."}[5m]) /
          rate(mw_http_requests_total[5m]) > 0.05
        for: 2m
        labels:
          severity: critical
        annotations:
          description: "Error rate > 5% for 2 minutes"

      # Circuit breaker open
      - alert: CircuitBreakerOpen
        expr: mw_circuit_breaker_state{service="odoo"} == 1
        for: 1m
        labels:
          severity: critical
        annotations:
          description: "Odoo circuit breaker open - orders not syncing"

      # Queue backlog
      - alert: QueueBacklog
        expr: mw_celery_queue_length > 500
        for: 5m
        labels:
          severity: warning
        annotations:
          description: "Queue depth > 500 for 5 minutes"

      # Outbox stale
      - alert: OutboxStale
        expr: mw_outbox_oldest_pending_age_seconds > 600
        for: 2m
        labels:
          severity: critical
        annotations:
          description: "Oldest outbox entry > 10 minutes old"

      # Dead letter spike
      - alert: DeadLetterSpike
        expr: |
          rate(mw_order_sync_attempts_total{result="dead_letter"}[5m]) > 0.1
        for: 5m
        labels:
          severity: warning
        annotations:
          description: "Dead letter rate > 0.1/s for 5 minutes"
```

### Log Aggregation

**Fluentd Configuration:**

```conf
# fluent.conf
<source>
  @type forward
  port 24224
</source>

<filter docker.**>
  @type parser
  key_name log
  <parse>
    @type json
    time_key timestamp
    time_format %Y-%m-%dT%H:%M:%S.%LZ
  </parse>
</filter>

<match docker.middleware.**>
  @type elasticsearch
  host elasticsearch
  port 9200
  index_name middleware-logs
  type_name _doc
  logstash_format true
  logstash_prefix middleware
  <buffer>
    flush_interval 10s
  </buffer>
</match>
```

---

## 🔧 Maintenance Procedures

### Database Maintenance

**Weekly VACUUM:**
```bash
# Run during low-traffic window (2-4 AM)
docker exec postgres psql -U middleware -d middleware_db -c "VACUUM ANALYZE;"
```

**Monthly Index Rebuild:**
```bash
# Rebuild indexes to reduce bloat
docker exec postgres psql -U middleware -d middleware_db -c "REINDEX DATABASE middleware_db;"
```

### Redis Maintenance

**Weekly Persistence Check:**
```bash
# Verify AOF integrity
docker exec redis redis-check-aof /data/appendonly.aof

# Manual save (if needed)
docker exec redis redis-cli BGSAVE
```

### Log Rotation

**Logrotate Configuration:**
```conf
# /etc/logrotate.d/middleware
/var/log/middleware/*.log {
    daily
    rotate 7
    compress
    delaycompress
    missingok
    notifempty
    create 0640 appuser appuser
    sharedscripts
    postrotate
        docker-compose -f /opt/middleware/docker-compose.prod.yml kill -s USR1 api
    endscript
}
```

---

## 📝 Deployment Checklist

### Pre-Deployment

- [ ] Code review approved
- [ ] All tests passing (unit + integration)
- [ ] Security scan clean
- [ ] Database migration tested in staging
- [ ] Rollback plan documented
- [ ] Monitoring dashboards ready
- [ ] On-call engineer assigned
- [ ] Business stakeholders notified (if breaking changes)

### During Deployment

- [ ] Green stack deployed
- [ ] Database migrations applied
- [ ] Smoke tests passed
- [ ] Traffic switched (10% → 50% → 100%)
- [ ] Metrics monitored (error rate, latency, queue depth)
- [ ] No alerts triggered

### Post-Deployment

- [ ] All services healthy
- [ ] Recent orders syncing correctly
- [ ] No dead letters
- [ ] Circuit breakers closed
- [ ] Logs clean (no errors)
- [ ] Blue stack stopped (after 1h)
- [ ] Deployment documented in changelog
- [ ] Team notified in Slack

---

## 🆘 Emergency Procedures

### Complete System Failure

**1. Immediate Actions:**
```bash
# Enable dry-run mode (stop writes to Odoo/platforms)
docker exec api-1 sh -c 'echo "MIDDLEWARE_DRY_RUN=true" >> /app/.env'
docker-compose restart api

# Alert team
curl -X POST ${SLACK_WEBHOOK_URL} \
  -d '{"text": "🚨 EMERGENCY: Middleware in dry-run mode"}'
```

**2. Investigate:**
```bash
# Check all services
docker-compose ps

# Check logs
docker-compose logs --tail=100 api worker-high

# Check database
docker exec postgres pg_isready
```

**3. Recovery:**
```bash
# Restore from backup (if DB corrupted)
gunzip < /backups/middleware_20260529.sql.gz | \
  docker exec -i postgres psql -U middleware middleware_db

# Restart services
docker-compose down
docker-compose up -d

# Disable dry-run
docker exec api-1 sh -c 'sed -i "s/MIDDLEWARE_DRY_RUN=true/MIDDLEWARE_DRY_RUN=false/" /app/.env'
docker-compose restart api
```

### Data Loss Prevention

**Outbox Replay:**
```bash
# Replay failed outbox entries
docker exec api-1 python -m src.scripts.replay_outbox \
  --start-time "2026-05-29 10:00:00" \
  --end-time "2026-05-29 11:00:00"
```

**Manual Reconciliation:**
```bash
# Trigger reconciliation job manually
docker exec worker-high-1 celery -A src.workers.app call \
  src.workers.reconciliation_worker.reconcile_orders \
  --kwargs='{"platform": "shopee", "hours": 24}'
```

---

## 📚 References

- **Architecture:** `docs/02_ARCHITECTURE.md`
- **Environment Setup:** `docs/08_ENVIRONMENT_DEPLOYMENT.md`
- **CI/CD:** `docs/12_CICD.md`
- **Runbook:** `docs/14_RUNBOOK.md`
- **Monitoring:** `docs/11_OBSERVABILITY.md`

**External Resources:**
- [Docker Production Best Practices](https://docs.docker.com/develop/dev-best-practices/)
- [PostgreSQL High Availability](https://www.postgresql.org/docs/current/high-availability.html)
- [AWS Secrets Manager](https://docs.aws.amazon.com/secretsmanager/)
- [Prometheus Alerting](https://prometheus.io/docs/alerting/latest/overview/)
