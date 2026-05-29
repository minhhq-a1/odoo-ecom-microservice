"""Stock worker — sync stock to platforms."""
from __future__ import annotations

from celery import shared_task

from src.core.config import settings
from src.core.exceptions import (
    CircuitOpenError,
    OdooConnectionError,
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


@shared_task(name="workers.sync_stock_for_sku", bind=True, max_retries=MAX_RETRIES)
def sync_stock_for_sku(
    self, odoo_sku: str, platforms: list[str] | None = None,
) -> dict:
    async def _run() -> dict:
        from src.connectors.shopee import ShopeeConnector
        from src.schemas.unified import Platform, StockUpdateRequest
        from src.services.mapping_service import MappingService
        from src.services.stock_service import StockService

        svc = StockService()
        results: dict[str, dict] = {}
        target_platforms = platforms or ["shopee"]

        for platform in target_platforms:
            mappings = await MappingService.find_mappings_using_component(
                platform, odoo_sku,
            )
            if not mappings:
                results[platform] = {"status": "no_mapping", "skipped": True}
                continue

            per_platform: list[dict] = []
            for m in mappings:
                if m["mapping_type"] == "simple":
                    qty = await svc.calculate_platform_stock(m["odoo_sku"], platform)
                else:
                    qty = await svc.calculate_bundle_stock(m["platform_sku_id"], platform)

                if settings.MIDDLEWARE_DRY_RUN:
                    logger.info("dry_run_stock_skip",
                                odoo_sku=odoo_sku, platform=platform,
                                platform_sku=m["platform_sku_id"], qty=qty)
                    per_platform.append({"sku": m["platform_sku_id"], "qty": qty, "dry_run": True})
                    continue

                if platform == "shopee":
                    async with ShopeeConnector() as conn:
                        await conn.update_stock(StockUpdateRequest(
                            platform=Platform.SHOPEE, sku=m["platform_sku_id"],
                            quantity=qty, reason=f"sync_for_{odoo_sku}",
                        ))
                per_platform.append({"sku": m["platform_sku_id"], "qty": qty})
            results[platform] = {"updates": per_platform}
        return results

    try:
        return run_async(_run())
    except CircuitOpenError as e:
        countdown = circuit_retry_countdown(circuit_service_from_error(e))
        logger.warning("circuit_open_retry_scheduled_stock",
                       odoo_sku=odoo_sku, countdown_sec=countdown, error=str(e))
        raise self.retry(exc=e, countdown=countdown)
    except ShopeeRateLimitError as e:
        countdown = getattr(e, "retry_after", None) or SHOPEE_RL_COUNTDOWN_FALLBACK
        logger.warning("shopee_rate_limit_retry_scheduled_stock",
                       odoo_sku=odoo_sku, countdown_sec=countdown)
        raise self.retry(exc=e, countdown=countdown)
    except OdooConnectionError as e:
        logger.warning("odoo_connection_retry_scheduled_stock",
                       odoo_sku=odoo_sku,
                       countdown_sec=ODOO_CONN_COUNTDOWN, error=str(e))
        raise self.retry(exc=e, countdown=ODOO_CONN_COUNTDOWN)


@shared_task(name="scheduled.stock_safety_net")
def stock_safety_net() -> dict:
    """Iterate all active product_mappings every 15 minutes — safety net."""
    if not settings.ENABLE_STOCK_SYNC:
        return {"status": "disabled"}

    async def _run() -> dict:
        from src.services.mapping_service import MappingService
        platforms = ["shopee"]
        total = 0
        for platform in platforms:
            mappings = await MappingService.list_active_for_platform(platform)
            enqueued: set[tuple[str, str]] = set()
            for m in mappings:
                skus: list[str] = []
                if m["mapping_type"] == "bundle":
                    comps = await MappingService.get_bundle_components(m["id"])
                    skus = [c["odoo_sku"] for c in comps if c.get("odoo_sku")]
                if not skus:
                    skus = [m["odoo_sku"]]
                for sku in skus:
                    key = (platform, sku)
                    if key in enqueued:
                        continue
                    enqueued.add(key)
                    sync_stock_for_sku.apply_async(
                        kwargs={"odoo_sku": sku, "platforms": [platform]},
                        queue="stock.sync",
                    )
                    total += 1
        logger.info("stock_safety_net_queued", count=total)
        return {"queued": total}

    return run_async(_run())
