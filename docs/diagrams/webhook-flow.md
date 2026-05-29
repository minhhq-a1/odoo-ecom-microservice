# Webhook Flow Diagram

## Overview
This diagram shows the complete flow from receiving a webhook from Shopee to syncing the order in Odoo.

```mermaid
sequenceDiagram
    participant Shopee as Shopee API
    participant LB as Load Balancer
    participant API as FastAPI Server
    participant PG as PostgreSQL
    participant Redis as Redis Queue
    participant Worker as Celery Worker
    participant Odoo as Odoo 18

    Note over Shopee,Odoo: Happy Path - Order Created Webhook

    Shopee->>LB: POST /webhook/shopee<br/>(order created event)
    LB->>API: Forward request

    API->>API: Verify HMAC signature
    alt Invalid signature
        API-->>Shopee: 401 Unauthorized
    end

    API->>API: Check replay nonce<br/>(Redis cache)
    alt Duplicate nonce
        API-->>Shopee: 200 OK (idempotent)
    end

    API->>PG: INSERT webhook_outbox<br/>(status=pending)
    Note over API,PG: Transactional Outbox Pattern<br/>Ensures no event loss

    API->>Redis: LPUSH queue:orders.created<br/>(best-effort)
    alt Redis down
        Note over API,Redis: Relay job will retry<br/>every 30s from outbox
    end

    API-->>Shopee: 200 OK<br/>(webhook accepted)
    Note over API,Shopee: Fast response < 3s<br/>Prevents timeout

    Redis->>Worker: BRPOP queue:orders.created
    Worker->>PG: UPDATE webhook_outbox<br/>(status=processing)

    Worker->>Worker: Transform to UnifiedOrder
    Worker->>PG: Check OrderMapping<br/>(duplicate detection)

    alt Order already synced
        Worker->>PG: UPDATE webhook_outbox<br/>(status=completed)
        Note over Worker: Idempotent - skip
    end

    Worker->>Odoo: XML-RPC: search sale.order<br/>(by x_platform_order_id)

    alt Circuit breaker open
        Worker->>Worker: Retry with backoff
        Worker->>PG: UPDATE webhook_outbox<br/>(status=pending, retry_count++)
        Note over Worker: Max 8 retries<br/>Exponential backoff
    end

    Odoo-->>Worker: Order not found

    Worker->>Odoo: XML-RPC: create sale.order<br/>(with order lines)
    Odoo-->>Worker: order_id=12345

    Worker->>PG: INSERT order_mapping<br/>(platform_order_id → odoo_order_id)
    Worker->>PG: UPDATE webhook_outbox<br/>(status=completed)

    Note over Worker,Odoo: Order synced successfully
```

## Error Handling Flows

### Circuit Breaker Open

```mermaid
sequenceDiagram
    participant Worker as Celery Worker
    participant CB as Circuit Breaker
    participant Odoo as Odoo 18
    participant PG as PostgreSQL
    participant Slack as Slack Alert

    Worker->>CB: Check state
    CB-->>Worker: State = OPEN

    Worker->>Worker: Calculate retry countdown<br/>(based on service)
    Note over Worker: Odoo: 60s<br/>Shopee: 30s

    Worker->>PG: UPDATE webhook_outbox<br/>(status=pending, retry_count++)
    Worker->>Slack: Alert: Circuit breaker open

    Worker->>Worker: Retry after countdown

    Note over Worker,Odoo: After cooldown period

    Worker->>CB: Check state
    CB-->>Worker: State = HALF_OPEN

    Worker->>Odoo: Test request
    alt Success
        CB->>CB: Transition to CLOSED
        Worker->>Worker: Resume normal processing
    else Failure
        CB->>CB: Transition back to OPEN
        Worker->>Worker: Retry with backoff
    end
```

### Dead Letter Handling

```mermaid
sequenceDiagram
    participant Worker as Celery Worker
    participant PG as PostgreSQL
    participant Slack as Slack Alert
    participant Admin as Admin UI

    Worker->>Worker: Max retries exceeded (8)

    Worker->>PG: UPDATE webhook_outbox<br/>(status=dead_letter)
    Worker->>PG: UPDATE order_mapping<br/>(status=dead_letter, last_error)

    Worker->>Slack: Alert: Dead letter<br/>(platform, order_id, error)

    Note over Worker,Admin: Manual intervention required

    Admin->>PG: Query dead letter orders
    Admin->>Admin: Investigate root cause

    alt Fixable (e.g., product mapping missing)
        Admin->>PG: Fix data (add product mapping)
        Admin->>Worker: Trigger manual retry
        Worker->>Worker: Reprocess from outbox
    else Not fixable (e.g., invalid data)
        Admin->>PG: Mark as permanently failed
        Note over Admin: Document in incident log
    end
```

## Outbox Relay Job

```mermaid
sequenceDiagram
    participant Beat as Celery Beat
    participant Worker as Relay Worker
    participant PG as PostgreSQL
    participant Redis as Redis Queue

    Note over Beat,Redis: Runs every 30 seconds

    Beat->>Worker: Trigger relay_outbox_to_queue

    Worker->>PG: SELECT * FROM webhook_outbox<br/>WHERE status=pending<br/>AND retry_count < 8<br/>LIMIT 100

    loop For each pending entry
        Worker->>Redis: LPUSH queue:orders.created
        alt Redis success
            Worker->>PG: UPDATE webhook_outbox<br/>(queued_at=now)
        else Redis down
            Note over Worker: Skip, will retry in 30s
        end
    end

    Note over Worker,Redis: Ensures no event loss<br/>even if Redis was down
```

## Reconciliation Flow

```mermaid
sequenceDiagram
    participant Beat as Celery Beat
    participant Worker as Reconciliation Worker
    participant Shopee as Shopee API
    participant PG as PostgreSQL
    participant Odoo as Odoo 18
    participant Slack as Slack Alert

    Note over Beat,Slack: Runs daily at 2:00 AM

    Beat->>Worker: Trigger reconcile_orders

    Worker->>Shopee: GET /orders<br/>(last 24 hours)
    Shopee-->>Worker: 150 orders

    Worker->>PG: SELECT order_mapping<br/>(last 24 hours)
    PG-->>Worker: 148 orders

    Worker->>Worker: Compare order lists
    Note over Worker: Missing: 2 orders<br/>Status drift: 3 orders

    loop For each missing order
        Worker->>Worker: Fetch order details
        Worker->>Worker: Sync to Odoo<br/>(same as webhook flow)
        Worker->>PG: INSERT reconciliation_log<br/>(action=auto_fix)
    end

    loop For each status drift
        Worker->>Odoo: Update order status
        Worker->>PG: INSERT reconciliation_log<br/>(action=status_sync)
    end

    Worker->>Slack: Report: 2 missing fixed,<br/>3 status synced

    Note over Worker,Slack: Reconciliation complete
```

## Key Design Principles

1. **Transactional Outbox Pattern**
   - Webhook saved to PostgreSQL BEFORE Redis push
   - Guarantees no event loss even if Redis is down
   - Relay job ensures eventual delivery

2. **Idempotency**
   - Replay nonce check (Redis cache, 5 min TTL)
   - OrderMapping duplicate detection
   - Safe to retry any operation

3. **Circuit Breaker**
   - Protects Odoo from cascading failures
   - Automatic recovery with half-open state
   - Per-service configuration (Odoo, Shopee)

4. **Observability**
   - Every step logged with structured logging
   - Prometheus metrics at each stage
   - Slack alerts for critical failures

5. **Graceful Degradation**
   - Fast webhook response (< 3s)
   - Async processing in background
   - Reconciliation job as safety net
