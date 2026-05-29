# RUNBOOK — Operations Guide
## Incident Response · Troubleshooting · Recovery Procedures

**Last Updated:** 2026-05-29
**Audience:** Ops Team, On-Call Engineers
**Scope:** Production incident response for Odoo E-Commerce Middleware

---

## 🚨 Emergency Contacts

| Role | Contact | Escalation |
|------|---------|------------|
| On-Call Engineer | Slack #ops-oncall | Page via PagerDuty |
| Tech Lead | Slack DM | Phone (emergency only) |
| DevOps Team | Slack #devops | — |
| Business Stakeholder | Email | — |

**Incident Severity:**
- **P0 (Critical):** Revenue impact, data loss, security breach → Page immediately
- **P1 (High):** Degraded service, high error rate → Alert + ticket
- **P2 (Medium):** Partial functionality loss → Ticket
- **P3 (Low):** Cosmetic issues, monitoring alerts → Ticket

---

## 📊 Health Check Dashboard

**Quick Status URLs:**
- Health: `https://middleware.example.com/health/ready`
- Metrics: `https://middleware.example.com/metrics`
- Admin UI: `https://middleware.example.com/admin/` (requires auth)
- Grafana: `https://grafana.example.com/d/middleware-overview`

**Key Metrics to Watch:**
- `mw_outbox_oldest_pending_age_seconds` < 300 (5 phút)
- `mw_order_sync_attempts_total{result="success"}` rate > 95%
- `mw_circuit_breaker_state{service="odoo"}` = 0 (closed)
- `mw_webhook_received_total` rate matches platform traffic

---

## 🔥 Common Incidents

### 1. Circuit Breaker Open (Odoo Down)

**Symptom:**
- Metric: `mw_circuit_breaker_state{service="odoo"} = 1` (open)
- Logs: `circuit_open` events with `service=odoo`
- Admin UI: Orders stuck in "pending" or "failed" status

**Root Cause:**
- Odoo server down or slow (5+ failures in 60s)
- Network connectivity issue
- PgBouncer connection pool exhausted
- Odoo database locked/slow queries

**Diagnosis:**
```bash
# 1. Check Odoo health
curl http://odoo:8069/web/health
# Expected: HTTP 200 with {"status": "pass"}

# 2. Check PgBouncer
docker logs pgbouncer | tail -50
# Look for: connection errors, pool exhausted

# 3. Check Odoo logs
docker logs odoo18c-app | grep -i error | tail -50

# 4. Check circuit breaker state
curl http://middleware:8000/metrics | grep circuit_breaker_state
# mw_circuit_breaker_state{service="odoo"} 1.0 = OPEN
```

**Resolution:**

**Option A: Odoo is healthy (false positive)**
```bash
# Manual circuit reset (emergency only)
redis-cli -h redis -p 6379
> DEL circuit:odoo:state
> DEL circuit:odoo:failures
> EXIT

# Circuit will auto-transition to half-open after 60s
# Monitor: should close after 1 successful request
```

**Option B: Odoo is down**
```bash
# 1. Restart Odoo
docker restart odoo18c-app

# 2. Wait 60s for circuit to transition to half-open
# 3. Circuit will auto-close after 1 successful request

# 4. If Odoo still down, check database
docker exec -it odoo18c-db psql -U odoo -d odoo18c-prod
# Check for locks: SELECT * FROM pg_stat_activity WHERE state = 'active';
```

**Option C: PgBouncer pool exhausted**
```bash
# Check pool stats
docker exec -it pgbouncer psql -p 6432 -U pgbouncer pgbouncer
> SHOW POOLS;
# Look for: cl_waiting > 0, maxwait > 5s

# Increase pool size (temporary)
# Edit docker-compose.yml: PGBOUNCER_MAX_CLIENT_CONN=200
docker-compose up -d pgbouncer
```

**Prevention:**
- Monitor Odoo response time (alert if P95 > 2s)
- Set up Odoo read replica for reporting queries
- Increase PgBouncer pool size if needed

---

### 2. Webhook Backlog (Outbox Pending)

**Symptom:**
- Metric: `mw_outbox_oldest_pending_age_seconds > 300` (5 phút)
- Metric: `mw_outbox_pending_count{status="pending"} > 100`
- Admin UI: Large number of pending entries in outbox table

**Root Cause:**
- Celery workers down or overloaded
- Redis broker down or slow
- Circuit breaker open (blocking worker processing)
- Database connection pool exhausted

**Diagnosis:**
```bash
# 1. Check Celery workers
docker ps | grep celery
# Expected: 4 workers running (2 order, 1 stock, 1 scheduled)

# 2. Check worker logs
docker logs middleware-worker-order-1 | tail -50
# Look for: errors, exceptions, stuck tasks

# 3. Check Redis broker
redis-cli -h redis -p 6379 -n 1 PING
# Expected: PONG

# 4. Check queue depths
redis-cli -h redis -p 6379 -n 1
> LLEN orders.created.normal
> LLEN orders.created.high
> LLEN stock.sync
# High numbers (>1000) indicate backlog

# 5. Check outbox table
docker exec -it middleware-db psql -U middleware -d middleware_prod
> SELECT status, COUNT(*) FROM webhook_outbox GROUP BY status;
> SELECT MIN(created_at) FROM webhook_outbox WHERE status = 'pending';
```

**Resolution:**

**Option A: Workers down**
```bash
# Restart workers
docker-compose restart worker-order-1 worker-order-2 worker-stock worker-scheduled

# Verify workers are processing
docker logs -f middleware-worker-order-1
# Should see: "process_webhook_event_start" logs
```

**Option B: Redis down**
```bash
# Restart Redis
docker restart redis

# Outbox relay job will auto-retry pending entries every 30s
# Monitor: mw_outbox_oldest_pending_age_seconds should decrease
```

**Option C: Manual replay (emergency)**
```bash
# Via Admin UI (recommended)
# 1. Go to https://middleware.example.com/admin/
# 2. Click "Outbox" tab
# 3. Filter status=pending
# 4. Click "Retry All" (rate limited: 1/minute)

# Via psql (if Admin UI down)
docker exec -it middleware-db psql -U middleware -d middleware_prod
> UPDATE webhook_outbox SET status = 'pending', retry_count = 0
  WHERE status = 'failed' AND created_at > NOW() - INTERVAL '1 hour';
# Workers will auto-pick up pending entries
```

**Prevention:**
- Scale workers horizontally (add more worker containers)
- Monitor queue depths (alert if > 500)
- Set up Redis cluster for HA

---

### 3. Dead Letter Queue Spike

**Symptom:**
- Metric: `mw_outbox_dead_letter_total` increasing rapidly
- Metric: `mw_order_sync_attempts_total{result="dead_letter"}` > 5% of total
- Slack alert: "Dead letter spike detected"

**Root Cause:**
- Product SKU not found in Odoo (missing product mapping)
- Odoo validation error (duplicate order, invalid data)
- Permanent API error (auth failure, permission denied)

**Diagnosis:**
```bash
# 1. Check dead letter reasons
docker exec -it middleware-db psql -U middleware -d middleware_prod
> SELECT last_error, COUNT(*) FROM webhook_outbox
  WHERE status = 'dead_letter' AND created_at > NOW() - INTERVAL '1 hour'
  GROUP BY last_error ORDER BY COUNT(*) DESC LIMIT 10;

# 2. Check order mapping table
> SELECT last_error, COUNT(*) FROM order_mapping
  WHERE status = 'dead_letter' AND created_at > NOW() - INTERVAL '1 hour'
  GROUP BY last_error ORDER BY COUNT(*) DESC LIMIT 10;

# 3. Check logs for ProductNotFoundError
docker logs middleware-worker-order-1 | grep ProductNotFoundError | tail -20
```

**Resolution:**

**Case A: Missing product mapping**
```bash
# 1. Identify missing SKUs
docker logs middleware-worker-order-1 | grep "ProductNotFoundError: sku=" | \
  sed 's/.*sku=\([^ ]*\).*/\1/' | sort | uniq -c | sort -rn

# 2. Add product mappings via Admin UI
# Go to https://middleware.example.com/admin/config/products
# Click "New Mapping"
# Fill in: platform=shopee, platform_sku_id=XXX, odoo_sku=YYY

# 3. Retry dead letter entries
# Admin UI → Outbox → Filter status=dead_letter → Retry All
```

**Case B: Odoo validation error**
```bash
# 1. Check Odoo logs for validation errors
docker logs odoo18c-app | grep ValidationError | tail -20

# 2. Fix data issue in Odoo (e.g., create missing partner, product)

# 3. Retry failed orders via Admin UI
# Admin UI → Orders → Filter status=dead_letter → Retry individually
```

**Case C: Permanent API error (auth/permission)**
```bash
# 1. Check Odoo credentials
docker exec -it middleware-api env | grep ODOO_
# Verify: ODOO_URL, ODOO_USER, ODOO_PASSWORD

# 2. Test Odoo connection
docker exec -it middleware-api python3 -c "
from src.odoo.client import OdooClient
client = OdooClient()
print(client.uid)  # Should print user ID, not None
"

# 3. If auth fails, update credentials in .env and restart
docker-compose restart api worker-order-1 worker-order-2
```

**Prevention:**
- Pre-populate product mappings before go-live
- Set up alerts for dead_letter rate > 1%
- Weekly review of dead letter queue

---

### 4. Shopee Rate Limit Hit

**Symptom:**
- Metric: `mw_shopee_rate_limit_hits_total` increasing
- Logs: `shopee_rate_limit_retry_scheduled` with `countdown_sec=60`
- Orders/stock sync delayed

**Root Cause:**
- Too many API calls in short time (Shopee limit: varies by endpoint)
- Burst traffic (e.g., flash sale, bulk stock update)
- Retry storm (circuit breaker thrashing)

**Diagnosis:**
```bash
# 1. Check rate limit hits by endpoint
curl http://middleware:8000/metrics | grep shopee_rate_limit_hits_total

# 2. Check Shopee API call rate
curl http://middleware:8000/metrics | grep shopee_request_total | \
  awk '{print $1, $2}' | sort -k2 -rn | head -10

# 3. Check worker logs for retry backoff
docker logs middleware-worker-stock | grep shopee_rate_limit | tail -20
```

**Resolution:**

**Option A: Wait for rate limit window to reset**
```bash
# Shopee rate limits reset every 60s
# Workers will auto-retry with exponential backoff
# Monitor: rate limit hits should decrease after 1-2 minutes
```

**Option B: Reduce API call frequency (temporary)**
```bash
# 1. Pause stock safety net job (if running)
docker exec -it redis redis-cli -n 1
> DEL celery-beat-schedule
# This stops scheduled jobs until worker restart

# 2. Scale down stock workers (if too many)
docker-compose scale worker-stock=0
# Wait 5 minutes for rate limit to clear
docker-compose scale worker-stock=1
```

**Option C: Increase retry backoff (code change)**
```python
# src/workers/retry_policy.py
SHOPEE_RL_COUNTDOWN_FALLBACK = 120  # Increase from 60 to 120s
```

**Prevention:**
- Implement adaptive rate limiting (track remaining quota)
- Batch stock updates (update multiple SKUs in one API call if supported)
- Set up Shopee webhook for stock updates (reduce polling)

---

### 5. Reconciliation Job Failures

**Symptom:**
- Slack alert: "Reconciliation found X missing orders"
- Metric: `mw_reconciliation_needs_review_count > 10`
- Scheduled job logs show errors

**Root Cause:**
- Shopee API timeout (large date range)
- Missing orders not auto-fixable (cancelled, refunded status)
- Field drift (amount_total mismatch, tracking number mismatch)

**Diagnosis:**
```bash
# 1. Check reconciliation logs
docker logs middleware-worker-scheduled | grep reconciliation | tail -50

# 2. Check reconciliation_log table
docker exec -it middleware-db psql -U middleware -d middleware_prod
> SELECT run_date, orders_checked, orders_missing, auto_fixed, needs_review
  FROM reconciliation_log ORDER BY run_date DESC LIMIT 7;

# 3. Check details of missing orders
> SELECT details->'missing' FROM reconciliation_log
  WHERE run_date = CURRENT_DATE - 1;
```

**Resolution:**

**Case A: Auto-fix failed (safe to retry)**
```bash
# 1. Get list of missing order IDs from reconciliation_log
docker exec -it middleware-db psql -U middleware -d middleware_prod
> SELECT jsonb_array_elements(details->'missing')->>'platform_order_id'
  FROM reconciliation_log WHERE run_date = CURRENT_DATE - 1;

# 2. Manually trigger sync via Admin UI
# For each order ID:
# - Go to Admin UI → Orders → Search by platform_order_id
# - If not found, it's truly missing → Click "Manual Sync"
```

**Case B: Field drift (amount_total mismatch)**
```bash
# 1. Check drift details
docker exec -it middleware-db psql -U middleware -d middleware_prod
> SELECT details->'drifts' FROM reconciliation_log
  WHERE run_date = CURRENT_DATE - 1;

# 2. Investigate in Odoo
# - Compare Odoo sale.order.amount_total with Shopee order total
# - Common causes: shipping fee mismatch, voucher not applied, rounding

# 3. If Odoo is wrong, manually adjust in Odoo UI
# 4. If Shopee is wrong, contact Shopee support

# 5. Mark as reviewed (no action needed)
# Update reconciliation_log.details to add "reviewed": true
```

**Case C: Shopee API timeout**
```bash
# 1. Check Shopee connector timeout setting
docker exec -it middleware-api env | grep SHOPEE_TIMEOUT
# Default: 30s

# 2. Increase timeout (temporary)
# Edit .env: SHOPEE_TIMEOUT=60
docker-compose restart api worker-order-1

# 3. Re-run reconciliation manually
docker exec -it middleware-worker-scheduled python3 -c "
from src.workers.scheduled import run_reconciliation
run_reconciliation.apply_async()
"
```

**Prevention:**
- Run reconciliation for smaller date ranges (daily instead of weekly)
- Set up Shopee webhook for order updates (reduce reliance on reconciliation)
- Weekly review of reconciliation reports

---

## 🛠️ Maintenance Procedures

### Planned Downtime (Blue-Green Deployment)

**Pre-deployment checklist:**
- [ ] Announce maintenance window in #ops-announce (24h notice)
- [ ] Verify backup completed in last 1h
- [ ] Check current error rate < 1%
- [ ] Drain Celery queues (wait for queue depth = 0)

**Deployment steps:**
```bash
# 1. Deploy new version to green environment
docker-compose -f docker-compose.green.yml up -d

# 2. Run smoke tests
curl https://middleware-green.example.com/health/ready
# Expected: HTTP 200

# 3. Switch traffic (load balancer)
# Update nginx/ALB to point to green environment

# 4. Monitor for 10 minutes
# Watch metrics, logs, error rate

# 5. If OK, stop blue environment
docker-compose -f docker-compose.blue.yml down

# 6. If NOT OK, rollback (switch traffic back to blue)
```

### Database Migration

**Pre-migration checklist:**
- [ ] Backup database: `pg_dump middleware_prod > backup_$(date +%Y%m%d_%H%M%S).sql`
- [ ] Test migration on staging environment
- [ ] Estimate migration time (< 5 min for online, > 5 min requires downtime)

**Migration steps:**
```bash
# 1. Run migration (online, no downtime)
docker exec -it middleware-api alembic upgrade head

# 2. Verify migration
docker exec -it middleware-db psql -U middleware -d middleware_prod
> \dt  # List tables
> SELECT version_num FROM alembic_version;

# 3. If migration fails, rollback
docker exec -it middleware-api alembic downgrade -1
```

### Secret Rotation

**Rotate CREDENTIAL_KEYS (every 90 days):**
```bash
# 1. Generate new Fernet key
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# Output: NEW_KEY_HERE

# 2. Prepend to CREDENTIAL_KEYS in .env
# OLD: CREDENTIAL_KEYS=key1,key2
# NEW: CREDENTIAL_KEYS=NEW_KEY,key1,key2

# 3. Deploy (no downtime, old keys still work for decrypt)
docker-compose up -d

# 4. Re-encrypt all credentials
docker exec -it middleware-api python3 scripts/rotate_credentials.py

# 5. After 7 days, remove old keys from .env
# CREDENTIAL_KEYS=NEW_KEY
docker-compose up -d
```

**Rotate ADMIN_SECRET_TOKEN (every 30 days):**
```bash
# 1. Generate new token
openssl rand -hex 32

# 2. Update .env
# ADMIN_SECRET_TOKEN=NEW_TOKEN

# 3. Deploy
docker-compose restart api

# 4. Notify team to clear browser cookies
```

---

## 📞 Escalation Matrix

| Incident Type | First Response | Escalation (15 min) | Escalation (30 min) |
|---------------|----------------|---------------------|---------------------|
| Circuit breaker open | On-call engineer | Tech lead | DevOps manager |
| Webhook backlog | On-call engineer | Tech lead | — |
| Dead letter spike | On-call engineer | Tech lead | Business stakeholder |
| Data loss suspected | On-call engineer + Tech lead | CTO | Legal |
| Security breach | On-call engineer + Security team | CTO | Legal + PR |

---

## 📚 Additional Resources

- **Architecture:** `docs/02_ARCHITECTURE.md`
- **Observability:** `docs/11_OBSERVABILITY.md`
- **Deployment:** `docs/15_DEPLOYMENT.md` (see next)
- **Security:** `docs/10_SECURITY.md`
- **Grafana Dashboards:** `https://grafana.example.com/d/middleware-overview`
- **Slack Channels:** `#ops-oncall`, `#devops`, `#middleware-alerts`

---

**Document Version:** 1.0
**Last Reviewed:** 2026-05-29
**Next Review:** 2026-06-29 (monthly)
