"""Prometheus metrics."""
from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

WEBHOOK_RECEIVED = Counter(
    "mw_webhook_received_total", "Webhook events received",
    ["platform", "event_type"],
)
WEBHOOK_SIGNATURE_INVALID = Counter(
    "mw_webhook_signature_invalid_total", "Invalid signature events", ["platform"],
)
WEBHOOK_DURATION = Histogram(
    "mw_webhook_response_seconds", "Webhook handler duration",
    ["platform", "status_code"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5),
)

OUTBOX_PENDING = Gauge(
    "mw_outbox_pending_count", "Outbox entries by status", ["status"],
)
OUTBOX_PUBLISHED = Counter(
    "mw_outbox_published_total", "Outbox published", ["platform", "event_type"],
)
OUTBOX_DEAD_LETTER = Counter(
    "mw_outbox_dead_letter_total", "Outbox dead-letter", ["platform", "event_type"],
)
OUTBOX_OLDEST_AGE = Gauge(
    "mw_outbox_oldest_pending_age_seconds", "Age of oldest pending outbox entry",
)

ORDER_SYNC_ATTEMPTS = Counter(
    "mw_order_sync_attempts_total", "Order sync attempts", ["platform", "result"],
)
ORDER_SYNC_DURATION = Histogram(
    "mw_order_sync_duration_seconds", "Order sync duration", ["platform"],
    buckets=(0.5, 1, 2, 5, 10, 30, 60),
)

ODOO_REQUEST = Counter(
    "mw_odoo_request_total", "Odoo XML-RPC requests", ["method", "result"],
)
ODOO_DURATION = Histogram(
    "mw_odoo_request_duration_seconds", "Odoo request duration", ["method"],
    buckets=(0.1, 0.5, 1, 2, 5, 10, 30),
)

CB_STATE = Gauge(
    "mw_circuit_breaker_state", "Circuit breaker state 0=closed,1=open,2=half_open",
    ["service"],
)
CB_OPEN_TOTAL = Counter(
    "mw_circuit_breaker_open_total", "Circuit breaker opened", ["service"],
)

SHOPEE_REQUEST = Counter(
    "mw_shopee_request_total", "Shopee API requests", ["endpoint", "status"],
)
SHOPEE_RATE_LIMIT = Counter(
    "mw_shopee_rate_limit_hits_total", "Shopee rate limit hits", ["endpoint"],
)
SHOPEE_TOKEN_AGE = Gauge(
    "mw_shopee_token_age_seconds", "Shopee token age", ["shop_id", "token_type"],
)
