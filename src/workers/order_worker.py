"""Order worker — process webhook events + sync to Odoo."""

from __future__ import annotations

from typing import Any

from celery import shared_task

from src.core.exceptions import (
    CircuitOpenError,
    OdooConnectionError,
    ProductNotFoundError,
    ShopeeRateLimitError,
)
from src.core.logging import get_logger
from src.workers._async_helper import run_async
from src.workers.retry_policy import (
    MAX_RETRIES,
    ODOO_CONN_COUNTDOWN,
    SHOPEE_RL_COUNTDOWN_FALLBACK,
    circuit_retry_countdown,
    circuit_service_from_error,
)

logger = get_logger(__name__)


async def _process_logistics_event(
    outbox_id: int,
    platform: str,
    platform_order_id: str | None,
    payload: dict[str, Any],
) -> dict[str, Any]:
    if not platform_order_id:
        logger.warning("logistics_event_no_order_id", outbox_id=outbox_id)
        return {"status": "logistics_event_no_order_id"}

    data = payload.get("data", {}) or {}
    tracking_number = (
        payload.get("tracking_number")
        or data.get("tracking_number")
        or data.get("tracking_no")
        or data.get("tracking_number_list", [None])[0]
    )
    if not tracking_number:
        logger.info(
            "logistics_event_no_tracking",
            outbox_id=outbox_id,
            platform_order_id=platform_order_id,
        )
        return {"status": "logistics_event_no_tracking"}

    from src.odoo.client import OdooClient

    updated = await OdooClient().update_order_tracking(
        platform,
        platform_order_id,
        str(tracking_number),
    )
    if not updated:
        logger.warning(
            "logistics_event_order_not_found",
            platform=platform,
            platform_order_id=platform_order_id,
            outbox_id=outbox_id,
        )
        return {"status": "order_not_found"}

    logger.info(
        "tracking_number_updated",
        platform=platform,
        platform_order_id=platform_order_id,
        tracking_number=tracking_number,
    )
    return {"status": "tracking_updated", "tracking_number": str(tracking_number)}


@shared_task(name="workers.process_webhook_event", bind=True, max_retries=MAX_RETRIES)
def process_webhook_event(
    self,
    outbox_id: int,
    platform: str,
    event_type: str,
    platform_order_id: str | None,
    payload: dict[str, Any],
) -> dict[str, Any]:
    logger.info(
        "process_webhook_event_start", outbox_id=outbox_id, platform=platform, event_type=event_type
    )

    async def _run() -> dict[str, Any]:
        from src.connectors.shopee import ShopeeConnector
        from src.core.config import settings
        from src.services.order_service import OrderService

        if event_type == "order" and platform == "shopee":
            if settings.MIDDLEWARE_DRY_RUN:
                logger.info(
                    "dry_run_skip_shopee_fetch",
                    outbox_id=outbox_id,
                    platform_order_id=platform_order_id,
                )
                return {"status": "dry_run_skipped"}
            async with ShopeeConnector() as conn:
                order = await conn.get_order_detail(platform_order_id)
            return await OrderService().sync(order)
        if event_type == "stock":
            return {"status": "stock_event_ignored"}
        if event_type == "logistics":
            return await _process_logistics_event(outbox_id, platform, platform_order_id, payload)
        return {"status": "no_handler"}

    try:
        return run_async(_run())
    except ProductNotFoundError as e:
        logger.exception("product_not_found_dead_letter", sku=e.sku, outbox_id=outbox_id)
        # Persist dead-letter state on the durable rows so dashboards and
        # alerts see a non-retryable failure instead of an apparently
        # successful Celery result. Round 21 P2-21A: also flip the
        # OrderMapping row and fire the dead-letter alert path; otherwise
        # ops has no signal when a missing bundle component permanently
        # kills an order.
        sku = e.sku

        async def _mark_dead_letter() -> None:
            from sqlalchemy import select

            from src.core.database import get_async_db_context
            from src.models.order_mapping import OrderMapping
            from src.models.outbox import WebhookOutbox
            from src.services.outbox_service import OutboxService

            async with get_async_db_context() as db:
                outbox = await db.get(WebhookOutbox, outbox_id)
                if outbox is not None:
                    outbox.status = "dead_letter"
                    outbox.last_error = f"ProductNotFound: sku={sku}"
                if platform_order_id is not None:
                    mapping = (
                        await db.execute(
                            select(OrderMapping).where(
                                OrderMapping.platform == platform,
                                OrderMapping.platform_order_id == platform_order_id,
                            ),
                        )
                    ).scalar_one_or_none()
                    if mapping is not None:
                        mapping.status = "dead_letter"
                        mapping.last_error = f"ProductNotFound: sku={sku}"
                await db.commit()
            # Fire alert OUTSIDE the publish transaction (matches the
            # round-2 P2-E pattern used by OutboxService).
            await OutboxService._fire_dead_letter_alerts([outbox_id])

        try:
            run_async(_mark_dead_letter())
        except Exception as persist_err:
            logger.warning(
                "product_not_found_dead_letter_persist_failed",
                outbox_id=outbox_id,
                error=str(persist_err),
            )
        return {"status": "dead_letter", "reason": "product_not_found", "sku": sku}
    except CircuitOpenError as e:
        countdown = circuit_retry_countdown(circuit_service_from_error(e))
        logger.warning(
            "circuit_open_retry_scheduled",
            outbox_id=outbox_id,
            countdown_sec=countdown,
            error=str(e),
        )
        raise self.retry(exc=e, countdown=countdown)
    except ShopeeRateLimitError as e:
        countdown = getattr(e, "retry_after", None) or SHOPEE_RL_COUNTDOWN_FALLBACK
        logger.warning(
            "shopee_rate_limit_retry_scheduled", outbox_id=outbox_id, countdown_sec=countdown
        )
        raise self.retry(exc=e, countdown=countdown)
    except OdooConnectionError as e:
        logger.warning(
            "odoo_connection_retry_scheduled",
            outbox_id=outbox_id,
            countdown_sec=ODOO_CONN_COUNTDOWN,
            attempt=self.request.retries + 1,
            error=str(e),
        )
        raise self.retry(exc=e, countdown=ODOO_CONN_COUNTDOWN)


@shared_task(name="workers.sync_order_to_odoo", bind=True, max_retries=MAX_RETRIES)
def sync_order_to_odoo(self, order_mapping_id: int) -> dict[str, Any]:
    """Manual retry path from Admin UI."""

    async def _run() -> dict[str, Any]:
        from src.connectors.shopee import ShopeeConnector
        from src.core.config import settings
        from src.core.database import get_async_db_context
        from src.models.order_mapping import OrderMapping
        from src.services.order_service import OrderService

        async with get_async_db_context() as db:
            mapping = await db.get(OrderMapping, order_mapping_id)
            if not mapping:
                return {"status": "not_found"}
            if mapping.platform != "shopee":
                return {"status": "unsupported_platform"}
            order_sn = mapping.platform_order_id

        if settings.MIDDLEWARE_DRY_RUN:
            logger.info(
                "dry_run_skip_manual_retry",
                order_mapping_id=order_mapping_id,
                platform_order_id=order_sn,
            )
            return {"status": "dry_run_skipped"}

        async with ShopeeConnector() as conn:
            order = await conn.get_order_detail(order_sn)
        return await OrderService().sync(order)

    try:
        return run_async(_run())
    except CircuitOpenError as e:
        countdown = circuit_retry_countdown(circuit_service_from_error(e))
        logger.warning(
            "circuit_open_retry_scheduled_manual",
            order_mapping_id=order_mapping_id,
            countdown_sec=countdown,
            error=str(e),
        )
        raise self.retry(exc=e, countdown=countdown)
    except OdooConnectionError as e:
        logger.warning(
            "odoo_connection_retry_scheduled_manual",
            order_mapping_id=order_mapping_id,
            countdown_sec=ODOO_CONN_COUNTDOWN,
            error=str(e),
        )
        raise self.retry(exc=e, countdown=ODOO_CONN_COUNTDOWN)
