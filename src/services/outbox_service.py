"""Outbox service — try_publish_immediately + relay_pending."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from src.core.database import get_async_db_context
from src.core.logging import get_logger
from src.models.outbox import WebhookOutbox

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

# Lease applied when a row is claimed (status="processing"). The broker
# dispatch happens AFTER the claim commits, so a crash between commit and
# send would otherwise leave the row stuck in "processing". relay_pending
# reclaims any "processing" row whose lease has expired (process_after<=now).
PROCESSING_LEASE_SEC = 300


class OutboxService:
    @classmethod
    async def try_publish_immediately(cls, outbox_id: int) -> bool:
        dead_letter_ids: list[int] = []
        dispatch: dict | None = None
        # Claim phase: lock the row, transition it to "processing", commit.
        # The broker send is intentionally NOT done here — see _dispatch.
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
            dispatch = cls._prepare_publish(entry)
            if dispatch is None and entry.status == "dead_letter":
                dead_letter_ids.append(entry.id)
            await db.commit()

        # Dispatch phase: send to the broker only after the claim is durable,
        # then record the outcome in a fresh transaction.
        ok = False
        if dispatch is not None:
            ok, dl_id = await cls._dispatch(dispatch)
            if dl_id is not None:
                dead_letter_ids.append(dl_id)
        await cls._fire_dead_letter_alerts(dead_letter_ids)
        return ok

    @classmethod
    async def relay_pending(cls, batch_size: int = 100) -> dict[str, int]:
        stats = {"published": 0, "failed": 0, "skipped": 0, "scanned": 0}
        dead_letter_ids: list[int] = []
        dispatches: list[dict] = []
        # Claim phase: lock + transition the batch to "processing" and commit.
        # "processing" rows whose lease (process_after) has expired are
        # reclaimed here too, recovering any send that crashed mid-flight.
        # No broker I/O while the row locks are held — fixes the prior P3
        # where send_task ran for every row inside one long transaction.
        async with get_async_db_context() as db:
            now = datetime.now(UTC)
            stmt = (
                select(WebhookOutbox)
                .where(
                    WebhookOutbox.status.in_(["pending", "failed", "processing"]),
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
                dispatch = cls._prepare_publish(entry)
                if dispatch is None:
                    if entry.status == "dead_letter":
                        dead_letter_ids.append(entry.id)
                else:
                    dispatches.append(dispatch)
            await db.commit()

        # Dispatch phase: send each claimed row to the broker after commit.
        for dispatch in dispatches:
            ok, dl_id = await cls._dispatch(dispatch)
            if ok:
                stats["published"] += 1
            else:
                stats["failed"] += 1
                if dl_id is not None:
                    dead_letter_ids.append(dl_id)
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
    def _prepare_publish(cls, entry: WebhookOutbox) -> dict | None:
        """Claim a row for publishing (called inside the locking transaction).

        Marks it dead_letter and returns None if there is no queue mapping;
        otherwise transitions it to "processing" with a fresh lease and
        returns the dispatch descriptor used after commit. Does NOT touch the
        broker — that is deferred to `_dispatch` so the send never runs inside
        an open transaction that could later roll back.
        """
        queue = QUEUE_MAP.get((entry.platform, entry.event_type))
        if queue is None:
            entry.status = "dead_letter"
            entry.last_error = f"No queue mapping for {entry.platform}/{entry.event_type}"
            return None

        entry.status = "processing"
        entry.process_after = datetime.now(UTC) + timedelta(seconds=PROCESSING_LEASE_SEC)
        return {
            "outbox_id": entry.id,
            "queue": queue,
            "kwargs": {
                "outbox_id": entry.id,
                "platform": entry.platform,
                "event_type": entry.event_type,
                "platform_order_id": entry.platform_order_id,
                "payload": entry.payload,
            },
        }

    @classmethod
    async def _dispatch(cls, dispatch: dict) -> tuple[bool, int | None]:
        """Send a claimed row to the broker (AFTER its claim has committed),
        then record the outcome in a fresh transaction.

        Returns (ok, dead_letter_id). A worst-case duplicate send is harmless:
        the task_id `outbox-{id}` lets the broker dedupe and the consumer is
        idempotent (advisory lock + x_platform_order_id UNIQUE).
        """
        outbox_id = dispatch["outbox_id"]
        try:
            from src.workers.app import celery_app

            celery_app.send_task(
                "workers.process_webhook_event",
                kwargs=dispatch["kwargs"],
                queue=dispatch["queue"],
                task_id=f"outbox-{outbox_id}",
            )
        except Exception as e:
            return False, await cls._record_failure(outbox_id, str(e))

        await cls._record_published(outbox_id, dispatch["queue"])
        return True, None

    @classmethod
    async def _record_published(cls, outbox_id: int, queue: str) -> None:
        async with get_async_db_context() as db:
            entry = await db.get(WebhookOutbox, outbox_id)
            if entry is None:
                return
            entry.status = "published"
            entry.published_at = datetime.now(UTC)
            await db.commit()
        logger.info("outbox_published", outbox_id=outbox_id, queue=queue)

    @classmethod
    async def _record_failure(cls, outbox_id: int, error: str) -> int | None:
        """Record a failed dispatch. Returns the id if it was dead-lettered."""
        async with get_async_db_context() as db:
            entry = await db.get(WebhookOutbox, outbox_id)
            if entry is None:
                return None
            entry.retry_count += 1
            entry.last_error = error
            if entry.retry_count >= entry.max_retries:
                entry.status = "dead_letter"
                logger.error("outbox_dead_letter", outbox_id=outbox_id, error=error)
                await db.commit()
                return outbox_id
            entry.status = "failed"
            delay = RETRY_DELAYS_SEC[min(entry.retry_count - 1, len(RETRY_DELAYS_SEC) - 1)]
            entry.process_after = datetime.now(UTC) + timedelta(seconds=delay)
            logger.warning(
                "outbox_retry_scheduled",
                outbox_id=outbox_id,
                retry_count=entry.retry_count,
                delay_sec=delay,
            )
            await db.commit()
            return None

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
