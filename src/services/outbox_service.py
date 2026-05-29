"""Outbox service — try_publish_immediately + relay_pending."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import select

from src.core.database import get_async_db_context
from src.core.logging import get_logger
from src.models.outbox import WebhookOutbox

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

QUEUE_MAP: dict[tuple[str, str], str] = {
    ("shopee", "order"): "orders.created.normal",
    # Logistics and stock webhooks both go through process_webhook_event,
    # which is the same task body registered on the orders.created.normal
    # consumer. Routing them to dedicated queues would only work if those
    # queues had a worker registered for that task name; they don't (the
    # `shipment.confirm` and `stock.sync` queues are reserved for the
    # post-processing tasks `workers.confirm_shipment` /
    # `workers.sync_stock_for_sku`). Send everything that uses the
    # process_webhook_event task body to its actual consumer.
    ("shopee", "logistics"): "orders.created.normal",
    ("shopee", "stock"): "orders.created.normal",
    ("lazada", "order"): "orders.created.normal",
    ("tiktok", "order"): "orders.created.normal",
}

RETRY_DELAYS_SEC = (60, 300, 1800, 7200, 14400)


class OutboxService:
    @classmethod
    async def try_publish_immediately(cls, outbox_id: int) -> bool:
        dead_letter_ids: list[int] = []
        async with get_async_db_context() as db:
            stmt = (
                select(WebhookOutbox)
                .where(
                    WebhookOutbox.id == outbox_id,
                    WebhookOutbox.status == "pending",
                )
                .with_for_update(skip_locked=True)
            )
            entry = (await db.execute(stmt)).scalar_one_or_none()
            if entry is None:
                return False
            entry.status = "processing"
            await db.flush()
            ok = await cls._publish_entry(db, entry)
            if not ok and entry.status == "dead_letter":
                dead_letter_ids.append(entry.id)
            await db.commit()
        await cls._fire_dead_letter_alerts(dead_letter_ids)
        return ok

    @classmethod
    async def relay_pending(cls, batch_size: int = 100) -> dict[str, int]:
        stats = {"published": 0, "failed": 0, "skipped": 0, "scanned": 0}
        dead_letter_ids: list[int] = []
        async with get_async_db_context() as db:
            now = datetime.now(UTC)
            stmt = (
                select(WebhookOutbox)
                .where(
                    WebhookOutbox.status.in_(["pending", "failed"]),
                    WebhookOutbox.process_after <= now,
                    WebhookOutbox.retry_count < WebhookOutbox.max_retries,
                )
                .order_by(WebhookOutbox.created_at.asc())
                .limit(batch_size)
                .with_for_update(skip_locked=True)
            )
            result = await db.execute(stmt)
            entries = result.scalars().all()
            stats["scanned"] = len(entries)

            for entry in entries:
                entry.status = "processing"
                await db.flush()
                ok = await cls._publish_entry(db, entry)
                if ok:
                    stats["published"] += 1
                else:
                    stats["failed"] += 1
                    if entry.status == "dead_letter":
                        dead_letter_ids.append(entry.id)
            await db.commit()
        await cls._fire_dead_letter_alerts(dead_letter_ids)
        logger.info("outbox_relay_completed", **stats)
        return stats

    @classmethod
    async def manual_retry(cls, outbox_id: int) -> bool:
        async with get_async_db_context() as db:
            entry = await db.get(WebhookOutbox, outbox_id)
            if entry is None:
                return False
            entry.status = "pending"
            entry.process_after = datetime.now(UTC)
            entry.retry_count = 0
            entry.last_error = None
            await db.commit()
        return await cls.try_publish_immediately(outbox_id)

    @classmethod
    async def get_stats(cls) -> dict[str, int]:
        from sqlalchemy import func

        async with get_async_db_context() as db:
            result = await db.execute(
                select(WebhookOutbox.status, func.count()).group_by(WebhookOutbox.status),
            )
            return {row[0]: row[1] for row in result.all()}

    @classmethod
    async def _publish_entry(cls, db: AsyncSession, entry: WebhookOutbox) -> bool:
        queue = QUEUE_MAP.get((entry.platform, entry.event_type))
        if queue is None:
            entry.status = "dead_letter"
            entry.last_error = f"No queue mapping for {entry.platform}/{entry.event_type}"
            return False

        try:
            from src.workers.app import celery_app

            celery_app.send_task(
                "workers.process_webhook_event",
                kwargs={
                    "outbox_id": entry.id,
                    "platform": entry.platform,
                    "event_type": entry.event_type,
                    "platform_order_id": entry.platform_order_id,
                    "payload": entry.payload,
                },
                queue=queue,
                task_id=f"outbox-{entry.id}",
            )
            entry.status = "published"
            entry.published_at = datetime.now(UTC)
            logger.info("outbox_published", outbox_id=entry.id, queue=queue)
            return True
        except Exception as e:
            entry.retry_count += 1
            entry.last_error = str(e)
            if entry.retry_count >= entry.max_retries:
                entry.status = "dead_letter"
                logger.exception("outbox_dead_letter", outbox_id=entry.id, error=str(e))
            else:
                entry.status = "failed"
                delay = RETRY_DELAYS_SEC[min(entry.retry_count - 1, len(RETRY_DELAYS_SEC) - 1)]
                entry.process_after = datetime.now(UTC) + timedelta(seconds=delay)
                logger.warning(
                    "outbox_retry_scheduled",
                    outbox_id=entry.id,
                    retry_count=entry.retry_count,
                    delay_sec=delay,
                )
            return False

    @classmethod
    async def _fire_dead_letter_alerts(cls, outbox_ids: list[int]) -> None:
        """Send dead-letter alerts OUTSIDE the publish transaction.

        Codex round 2 P2-E: when this ran inside `_publish_entry`, an alert
        send failure rolled back the dead_letter status transition itself,
        causing the row to be re-tried + re-alerted in a loop.
        Read entries in a fresh transaction so the alert path can fail
        independently without losing status.
        """
        if not outbox_ids:
            return
        from src.services.alert_service import AlertService

        async with get_async_db_context() as db:
            entries = (
                (
                    await db.execute(
                        select(WebhookOutbox).where(WebhookOutbox.id.in_(outbox_ids)),
                    )
                )
                .scalars()
                .all()
            )
        for entry in entries:
            try:
                await AlertService.send_dead_letter_alert(entry)
            except Exception as e:
                logger.exception(
                    "outbox_dead_letter_alert_failed",
                    outbox_id=entry.id,
                    error=str(e),
                )
