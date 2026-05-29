"""Shipment confirmation worker."""

from __future__ import annotations

from celery import shared_task

from src.core.exceptions import ShopeeRateLimitError
from src.core.logging import get_logger
from src.workers._async_helper import run_async

logger = get_logger(__name__)


@shared_task(
    name="workers.confirm_shipment",
    bind=True,
    max_retries=5,
    autoretry_for=(ShopeeRateLimitError,),
    retry_backoff=True,
)
def confirm_shipment(self, platform: str, platform_order_id: str, tracking_no: str) -> dict:
    async def _run() -> dict:
        if platform == "shopee":
            from src.connectors.shopee import ShopeeConnector

            async with ShopeeConnector() as conn:
                await conn.confirm_shipment(platform_order_id, tracking_no)
        return {"status": "confirmed", "tracking_no": tracking_no}

    return run_async(_run())
