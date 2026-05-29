"""Price sync Odoo → platform."""

from __future__ import annotations

from celery import shared_task

from src.core.config import settings
from src.core.logging import get_logger
from src.workers._async_helper import run_async

logger = get_logger(__name__)


@shared_task(name="workers.sync_price_for_sku", bind=True, max_retries=3)
def sync_price_for_sku(self, sku: str, platform: str = "shopee") -> dict:
    async def _run() -> dict:
        if not settings.ENABLE_PRICE_SYNC:
            return {"status": "disabled"}
        from src.odoo.client import OdooClient
        from src.services.mapping_service import MappingService

        odoo = OdooClient()
        price = await odoo.get_product_price(sku)
        if price <= 0:
            logger.warning("price_sync_skip_zero_price", sku=sku, price=price)
            return {"status": "skipped_zero_price", "sku": sku}

        if settings.MIDDLEWARE_DRY_RUN:
            return {"status": "dry_run", "sku": sku, "price": price}

        if platform != "shopee":
            return {"status": "unsupported_platform", "platform": platform}

        from src.connectors.shopee import ShopeeConnector

        results: list[dict] = []
        for mapping in await MappingService.find_mappings_using_component(platform, sku):
            platform_sku = mapping.get("platform_sku_id")
            if not platform_sku:
                continue
            try:
                async with ShopeeConnector() as conn:
                    await conn.update_price(platform_sku, price)
                results.append({"platform_sku": platform_sku, "status": "synced"})
            except Exception as e:
                logger.exception(
                    "price_sync_failed",
                    sku=sku,
                    platform_sku=platform_sku,
                    error=str(e),
                )
                raise
        if not results:
            return {"status": "no_mapping", "sku": sku}
        return {"status": "synced", "sku": sku, "price": price, "updates": results}

    return run_async(_run())
