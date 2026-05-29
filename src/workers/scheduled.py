"""Beat-scheduled tasks: outbox relay, token refresh, reconciliation, cleanup."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from celery import shared_task
from sqlalchemy import delete, select

from src.core.config import settings
from src.core.logging import get_logger
from src.workers._async_helper import run_async

logger = get_logger(__name__)


@shared_task(name="scheduled.relay_outbox")
def relay_outbox() -> dict:
    from src.services.outbox_service import OutboxService

    return run_async(OutboxService.relay_pending(batch_size=200))


@shared_task(name="scheduled.cleanup_outbox")
def cleanup_outbox() -> dict:
    async def _run() -> dict:
        from sqlalchemy import update

        from src.core.database import get_async_db_context
        from src.models.outbox import WebhookOutbox
        from src.models.webhook_event_log import WebhookEventLog

        cutoff = datetime.now(UTC) - timedelta(days=7)
        async with get_async_db_context() as db:
            # webhook_event_log keeps a FK to webhook_outbox for audit, but
            # event logs are retained 30 days while outbox rows are pruned
            # after 7. NULL the FK on logs pointing at rows we're about to
            # delete so the delete doesn't violate referential integrity
            # (no ON DELETE behavior on the FK).
            target_ids = (
                (
                    await db.execute(
                        select(WebhookOutbox.id).where(
                            WebhookOutbox.status == "published",
                            WebhookOutbox.published_at < cutoff,
                        ),
                    )
                )
                .scalars()
                .all()
            )
            if not target_ids:
                return {"deleted": 0}
            await db.execute(
                update(WebhookEventLog)
                .where(WebhookEventLog.outbox_id.in_(target_ids))
                .values(outbox_id=None),
            )
            result = await db.execute(
                delete(WebhookOutbox).where(WebhookOutbox.id.in_(target_ids)),
            )
            await db.commit()
            return {"deleted": result.rowcount or 0}

    return run_async(_run())


@shared_task(name="scheduled.refresh_shopee_token")
def refresh_shopee_token() -> dict:
    async def _run() -> dict:
        from src.connectors.shopee import auth as shopee_auth
        from src.services.alert_service import AlertService

        shop_id = settings.SHOPEE_SHOP_ID
        try:
            await shopee_auth.refresh(shop_id)
            return {"status": "refreshed", "shop_id": shop_id}
        except Exception as e:
            logger.exception("token_refresh_failed", error=str(e), shop_id=shop_id)
            await AlertService.send_token_expiring(shop_id, 0)
            return {"status": "failed", "error": str(e)}

    return run_async(_run())


@shared_task(name="scheduled.polling_fallback")
def polling_fallback() -> dict:
    if not settings.ENABLE_POLLING_FALLBACK:
        return {"status": "disabled"}

    async def _run() -> dict:
        import time

        from src.connectors.shopee import ShopeeConnector
        from src.core.database import get_async_db_context
        from src.core.redis import get_redis
        from src.models.outbox import WebhookOutbox

        now = int(time.time())
        time_from = now - 15 * 60
        order_list: list[dict] = []
        cursor = ""
        async with ShopeeConnector() as conn:
            while True:
                data = await conn._request(
                    "GET",
                    "/api/v2/order/get_order_list",
                    params={
                        "time_range_field": "update_time",
                        "time_from": time_from,
                        "time_to": now,
                        "page_size": 100,
                        "cursor": cursor,
                    },
                )
                response = data.get("response", {}) or {}
                page = response.get("order_list", []) or []
                order_list.extend(page)
                if not response.get("more"):
                    break
                cursor = response.get("next_cursor", "")
                if not cursor:
                    break
        if not order_list:
            return {"polled": 0, "seeded": 0, "skipped": 0}

        # Dedup against the previous run (beat=10min, window=15min → 5min
        # overlap). Track (platform, order_sn, event_code) in Redis SET with
        # 30-minute TTL so a duplicate fallback poll within the overlap
        # doesn't re-seed the outbox and double the Shopee detail fetches.
        r = await get_redis()
        dedup_key = "polling_fallback:shopee:seen"
        seeded = 0
        skipped = 0
        async with get_async_db_context() as db:
            for o in order_list:
                order_sn = o.get("order_sn")
                if not order_sn:
                    continue
                member = f"shopee:{order_sn}:3"
                added = await r.sadd(dedup_key, member)
                if not added:
                    skipped += 1
                    continue
                db.add(
                    WebhookOutbox(
                        platform="shopee",
                        event_type="order",
                        event_code=3,
                        platform_order_id=order_sn,
                        payload={"code": 3, "data": o, "_polling": True},
                        signature="polling",
                        status="pending",
                    )
                )
                seeded += 1
            await db.commit()
        await r.expire(dedup_key, 30 * 60)
        logger.info(
            "polling_fallback_seeded",
            polled=len(order_list),
            seeded=seeded,
            skipped=skipped,
        )
        return {"polled": len(order_list), "seeded": seeded, "skipped": skipped}

    return run_async(_run())


@shared_task(name="scheduled.run_reconciliation")
def run_reconciliation() -> dict:
    if not settings.ENABLE_RECONCILIATION:
        return {"status": "disabled"}

    async def _run() -> dict:
        from src.services.reconciliation_service import ReconciliationService

        results = await ReconciliationService().run_all_platforms()
        return {
            "platforms": [
                {
                    "platform": r.platform,
                    "run_date": r.run_date.isoformat(),
                    "checked": r.orders_checked,
                    "matched": r.orders_matched,
                    "missing": r.orders_missing,
                    "auto_fixed": r.auto_fixed,
                    "needs_review": r.needs_review,
                    "drifts": len(r.drifts),
                }
                for r in results
            ],
        }

    return run_async(_run())


@shared_task(name="scheduled.retention_cleanup")
def retention_cleanup() -> dict:
    async def _run() -> dict:
        from src.core.database import get_async_db_context
        from src.models.order_sync_log import OrderSyncLog
        from src.models.webhook_event_log import WebhookEventLog

        now = datetime.now(UTC)
        deleted = {}
        async with get_async_db_context() as db:
            r1 = await db.execute(
                delete(WebhookEventLog).where(
                    WebhookEventLog.received_at < now - timedelta(days=30),
                ),
            )
            r2 = await db.execute(
                delete(OrderSyncLog).where(
                    OrderSyncLog.created_at < now - timedelta(days=90),
                ),
            )
            await db.commit()
            deleted["webhook_event_log"] = r1.rowcount or 0
            deleted["order_sync_log"] = r2.rowcount or 0
        return deleted

    return run_async(_run())
