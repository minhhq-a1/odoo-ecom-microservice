"""Slack / email alert service."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx

from src.core.config import settings
from src.core.logging import get_logger

if TYPE_CHECKING:
    from src.models.outbox import WebhookOutbox

logger = get_logger(__name__)


class AlertService:
    @staticmethod
    async def _post_slack(text: str) -> None:
        url = settings.SLACK_WEBHOOK_URL
        if not url:
            logger.warning("slack_webhook_not_configured", text=text)
            return
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                await client.post(url, json={"text": text})
        except Exception as e:
            logger.exception("slack_post_failed", error=str(e))

    @classmethod
    async def send_dead_letter_alert(cls, entry: WebhookOutbox) -> None:
        text = (
            f":skull: *Outbox dead letter*\n"
            f"id=`{entry.id}` platform=`{entry.platform}` "
            f"event=`{entry.event_type}` order_id=`{entry.platform_order_id}`\n"
            f"error: `{entry.last_error}`"
        )
        await cls._post_slack(text)

    @classmethod
    async def send_missing_sku(cls, sku: str) -> None:
        await cls._post_slack(f":warning: SKU `{sku}` not found in Odoo")

    @classmethod
    async def send_reconciliation_alert(cls, platform: str, needs_review: list) -> None:
        await cls._post_slack(
            f":mag: Reconciliation `{platform}` — {len(needs_review)} đơn cần review"
        )

    @classmethod
    async def send_token_expiring(cls, shop_id: str, days_left: int) -> None:
        await cls._post_slack(
            f":key: Shopee refresh_token shop_id=`{shop_id}` hết hạn sau {days_left} ngày"
        )
