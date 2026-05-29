"""Celery app instance + beat schedule."""
from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from src.core.config import settings
from src.core.logging import configure_logging

configure_logging()

celery_app = Celery(
    "middleware",
    broker=str(settings.CELERY_BROKER_URL),
    backend=str(settings.CELERY_RESULT_BACKEND),
    include=[
        "src.workers.order_worker",
        "src.workers.stock_worker",
        "src.workers.shipment_worker",
        "src.workers.price_worker",
        "src.workers.scheduled",
    ],
)

celery_app.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_track_started=True,
    task_time_limit=300,
    task_soft_time_limit=240,
    broker_connection_retry_on_startup=True,
    result_expires=86400,
    task_default_queue="default",
    task_routes={
        "workers.process_webhook_event": {"queue": "orders.created.normal"},
        "workers.sync_order_to_odoo": {"queue": "orders.created.normal"},
        "workers.sync_stock_for_sku": {"queue": "stock.sync"},
        "workers.confirm_shipment": {"queue": "shipment.confirm"},
        "workers.sync_price_for_sku": {"queue": "price.sync"},
        "scheduled.relay_outbox": {"queue": "default"},
        "scheduled.cleanup_outbox": {"queue": "default"},
        "scheduled.refresh_shopee_token": {"queue": "default"},
        "scheduled.run_reconciliation": {"queue": "reconciliation"},
        "scheduled.stock_safety_net": {"queue": "stock.sync"},
        "scheduled.polling_fallback": {"queue": "default"},
        "scheduled.retention_cleanup": {"queue": "default"},
    },
)

celery_app.conf.beat_schedule = {
    "outbox-relay-30s": {
        "task": "scheduled.relay_outbox",
        "schedule": 30.0,
    },
    "shopee-token-refresh-3h": {
        "task": "scheduled.refresh_shopee_token",
        "schedule": crontab(minute=17, hour="*/3"),
    },
    "stock-safety-net-15m": {
        "task": "scheduled.stock_safety_net",
        "schedule": crontab(minute="*/15"),
    },
    "polling-fallback-10m": {
        "task": "scheduled.polling_fallback",
        "schedule": crontab(minute="*/10"),
    },
    "reconciliation-nightly": {
        "task": "scheduled.run_reconciliation",
        "schedule": crontab(hour=2, minute=7),
    },
    "retention-cleanup-daily": {
        "task": "scheduled.retention_cleanup",
        "schedule": crontab(hour=3, minute=0),
    },
    "outbox-cleanup-daily": {
        "task": "scheduled.cleanup_outbox",
        "schedule": crontab(hour=3, minute=30),
    },
}
