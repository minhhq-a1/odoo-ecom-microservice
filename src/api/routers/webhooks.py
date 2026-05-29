"""Shopee + generic webhook receivers."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from fastapi import APIRouter, BackgroundTasks, Depends, Header, Request
from redis.exceptions import RedisError

from src.api.dependencies import get_async_db
from src.connectors.shopee.signing import verify_webhook
from src.core.config import settings
from src.core.logging import get_logger
from src.core.redis import get_redis
from src.models.outbox import WebhookOutbox
from src.models.webhook_event_log import WebhookEventLog
from src.monitoring.metrics import (
    WEBHOOK_RECEIVED,
    WEBHOOK_SIGNATURE_INVALID,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/webhook", tags=["webhooks"])
logger = get_logger(__name__)

EVENT_TYPE_MAP: dict[int, str] = {3: "order", 4: "logistics", 15: "stock"}


async def _is_replay(signature: str) -> bool:
    """Returns True only if Redis already has this signature recorded as
    successfully ingested. Uses GET (not SET nx) so a crash between
    durable commit and the post-commit `_mark_processed` call still lets
    Shopee's retry re-ingest the event — silent loss is worse than a
    duplicate (outbox + order_mapping idempotency dedup downstream).

    Redis failure → fail-open (treat as non-replay + log)."""
    try:
        r = await get_redis()
        nonce_key = f"webhook:nonce:shopee:{signature[:32]}"
        return await r.exists(nonce_key) == 1
    except RedisError as e:
        logger.warning("nonce_check_redis_unavailable_failopen", error=str(e))
        return False


async def _mark_processed(signature: str) -> None:
    """Record signature post-commit so future retries inside the 5-minute
    window short-circuit. Best-effort: Redis failure just means the next
    retry pays the full ingestion cost (still idempotent downstream)."""
    try:
        r = await get_redis()
        nonce_key = f"webhook:nonce:shopee:{signature[:32]}"
        await r.set(nonce_key, "1", ex=300)
    except RedisError as e:
        logger.warning("nonce_mark_redis_unavailable", error=str(e))


@router.post("/shopee")
async def shopee_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_async_db),
    x_shopee_signature: str | None = Header(default=None),
) -> dict[str, str]:
    body = await request.body()
    sig_valid = verify_webhook(body, x_shopee_signature or "", settings.SHOPEE_PARTNER_KEY)

    if sig_valid and x_shopee_signature and await _is_replay(x_shopee_signature):
        logger.warning("webhook_replay_detected", platform="shopee")
        return {"status": "ok", "replay": "true"}

    if not sig_valid:
        WEBHOOK_SIGNATURE_INVALID.labels(platform="shopee").inc()
        db.add(
            WebhookEventLog(
                platform="shopee",
                signature=x_shopee_signature,
                signature_valid=False,
                body_raw=body,
                body_size=len(body),
                source_ip=request.client.host if request.client else None,
                parse_error="invalid_signature",
            )
        )
        await db.commit()
        return {"status": "ok"}

    try:
        payload = json.loads(body)
        event_code = payload.get("code")
        data = payload.get("data", {}) or {}
        order_sn = data.get("ordersn") or data.get("order_sn")
    except (ValueError, TypeError) as e:
        db.add(
            WebhookEventLog(
                platform="shopee",
                signature=x_shopee_signature,
                signature_valid=True,
                body_raw=body,
                body_size=len(body),
                parse_error=str(e),
                source_ip=request.client.host if request.client else None,
            )
        )
        await db.commit()
        return {"status": "ok"}

    event_type = EVENT_TYPE_MAP.get(event_code, "unknown")
    WEBHOOK_RECEIVED.labels(platform="shopee", event_type=event_type).inc()

    if event_type == "unknown":
        # Round 10 P2: persist unknown signed events to audit log so
        # retention/observability sees them. No outbox row (no consumer).
        db.add(
            WebhookEventLog(
                platform="shopee",
                signature=x_shopee_signature,
                signature_valid=True,
                body_raw=body,
                body_size=len(body),
                event_code=event_code,
                platform_order_id=order_sn,
                source_ip=request.client.host if request.client else None,
                parse_error=f"unknown_event_code={event_code}",
            )
        )
        await db.commit()
        if x_shopee_signature:
            await _mark_processed(x_shopee_signature)
        logger.info("webhook_unknown_event", code=event_code)
        return {"status": "ok"}

    outbox = WebhookOutbox(
        platform="shopee",
        event_type=event_type,
        event_code=event_code,
        platform_order_id=order_sn,
        payload=payload,
        signature=x_shopee_signature,
        status="pending",
    )
    db.add(outbox)
    await db.flush()

    db.add(
        WebhookEventLog(
            platform="shopee",
            signature=x_shopee_signature,
            signature_valid=True,
            body_raw=body,
            body_size=len(body),
            event_code=event_code,
            platform_order_id=order_sn,
            outbox_id=outbox.id,
            source_ip=request.client.host if request.client else None,
        )
    )
    await db.commit()

    if x_shopee_signature:
        await _mark_processed(x_shopee_signature)

    from src.services.outbox_service import OutboxService

    background_tasks.add_task(OutboxService.try_publish_immediately, outbox.id)

    return {"status": "ok"}
