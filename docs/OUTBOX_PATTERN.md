# OUTBOX PATTERN — Implementation Guide
## Đảm bảo không mất webhook event dù Redis/Worker down

---

## Vấn đề cần giải quyết

```
HIỆN TẠI (rủi ro):
  Shopee Webhook → FastAPI → Redis Queue → Worker → Odoo
                                ↑
                         Nếu Redis down tại đây
                         → Event mất vĩnh viễn

VỚI OUTBOX:
  Shopee Webhook → FastAPI → PostgreSQL (outbox) → Worker → Odoo
                                  ↑                   ↑
                            Lưu trước        Relay job đọc từ DB
                            Không mất        Dù Redis restart vẫn ok
```

---

## Database Schema

```sql
-- migrations/versions/001_create_outbox.sql

CREATE TABLE webhook_outbox (
    id              BIGSERIAL PRIMARY KEY,
    platform        VARCHAR(20)  NOT NULL,           -- shopee | lazada | tiktok
    event_type      VARCHAR(50)  NOT NULL,           -- order | logistics | stock
    event_code      INTEGER,                         -- Shopee: 3=order, 4=logistics
    platform_order_id VARCHAR(100),
    payload         JSONB        NOT NULL,           -- Raw webhook payload
    signature       VARCHAR(255),                    -- Để verify lại nếu cần
    status          VARCHAR(20)  NOT NULL DEFAULT 'pending',
    -- pending | processing | published | failed | dead_letter
    retry_count     INTEGER      NOT NULL DEFAULT 0,
    max_retries     INTEGER      NOT NULL DEFAULT 5,
    last_error      TEXT,
    process_after   TIMESTAMPTZ  NOT NULL DEFAULT NOW(), -- Cho phép delay retry
    published_at    TIMESTAMPTZ,                     -- Khi nào push vào queue thành công
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- Index quan trọng cho relay job
CREATE INDEX idx_outbox_pending
    ON webhook_outbox (status, process_after)
    WHERE status IN ('pending', 'failed');

-- Index để tìm theo order
CREATE INDEX idx_outbox_platform_order
    ON webhook_outbox (platform, platform_order_id);

-- Tự động update updated_at
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN NEW.updated_at = NOW(); RETURN NEW; END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_outbox_updated_at
    BEFORE UPDATE ON webhook_outbox
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();
```

---

## SQLAlchemy Model

```python
# src/models/outbox.py

from sqlalchemy import (
    BigInteger, Column, DateTime, Index, Integer,
    String, Text, func, text
)
from sqlalchemy.dialects.postgresql import JSONB
from src.models.base import Base


class WebhookOutbox(Base):
    __tablename__ = "webhook_outbox"

    id                = Column(BigInteger, primary_key=True)
    platform          = Column(String(20),  nullable=False)
    event_type        = Column(String(50),  nullable=False)
    event_code        = Column(Integer)
    platform_order_id = Column(String(100))
    payload           = Column(JSONB,       nullable=False)
    signature         = Column(String(255))
    status            = Column(String(20),  nullable=False, default="pending")
    retry_count       = Column(Integer,     nullable=False, default=0)
    max_retries       = Column(Integer,     nullable=False, default=5)
    last_error        = Column(Text)
    process_after     = Column(DateTime(timezone=True), server_default=func.now())
    published_at      = Column(DateTime(timezone=True))
    created_at        = Column(DateTime(timezone=True), server_default=func.now())
    updated_at        = Column(DateTime(timezone=True), server_default=func.now(),
                               onupdate=func.now())

    __table_args__ = (
        Index(
            "idx_outbox_pending",
            "status", "process_after",
            postgresql_where=text("status IN ('pending', 'failed')")
        ),
        Index("idx_outbox_platform_order", "platform", "platform_order_id"),
    )

    def __repr__(self):
        return (
            f"<WebhookOutbox id={self.id} platform={self.platform} "
            f"event={self.event_type} status={self.status}>"
        )
```

---

## Webhook Receiver (FastAPI)

```python
# src/api/routers/webhooks.py

import hashlib
import hmac
import structlog
from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import settings
from src.core.database import get_async_db
from src.models.outbox import WebhookOutbox
from src.services.outbox_service import OutboxService

router = APIRouter(prefix="/webhook", tags=["webhooks"])
logger = structlog.get_logger(__name__)


@router.post("/shopee")
async def shopee_webhook(
    request:          Request,
    background_tasks: BackgroundTasks,
    db:               AsyncSession = Depends(get_async_db),
    x_shopee_signature: str = Header(None),
):
    body = await request.body()

    # ── Step 1: Xác thực signature ───────────────────────────────────
    if not _verify_shopee_signature(body, x_shopee_signature):
        logger.warning("shopee_webhook_invalid_signature",
                       signature=x_shopee_signature)
        # Vẫn trả 200 để Shopee không retry liên tục
        # nhưng KHÔNG lưu vào outbox
        return {"status": "ok"}

    # ── Step 2: Parse payload ────────────────────────────────────────
    try:
        payload    = await request.json()
        event_code = payload.get("code")
        shop_id    = payload.get("shop_id")
        data       = payload.get("data", {})
        order_sn   = data.get("ordersn") or data.get("order_sn")
    except Exception as e:
        logger.error("shopee_webhook_parse_error", error=str(e))
        return {"status": "ok"}  # Không raise, tránh Shopee retry

    # ── Step 3: Map event code → event type ─────────────────────────
    EVENT_TYPE_MAP = {3: "order", 4: "logistics", 15: "stock"}
    event_type = EVENT_TYPE_MAP.get(event_code, "unknown")

    if event_type == "unknown":
        logger.info("shopee_webhook_unknown_event", code=event_code)
        return {"status": "ok"}

    # ── Step 4: Lưu vào Outbox (SYNC - quan trọng nhất) ─────────────
    outbox_entry = WebhookOutbox(
        platform          = "shopee",
        event_type        = event_type,
        event_code        = event_code,
        platform_order_id = order_sn,
        payload           = payload,
        signature         = x_shopee_signature,
        status            = "pending",
    )
    db.add(outbox_entry)
    await db.commit()
    await db.refresh(outbox_entry)

    logger.info("shopee_webhook_saved_to_outbox",
                outbox_id=outbox_entry.id,
                event_type=event_type,
                order_sn=order_sn)

    # ── Step 5: Trigger relay ngay lập tức (background, best-effort) ─
    # Nếu Redis đang ok → xử lý ngay, không cần chờ relay job
    background_tasks.add_task(
        OutboxService.try_publish_immediately, outbox_entry.id
    )

    # ── Step 6: Trả 200 ngay cho Shopee ─────────────────────────────
    # Shopee yêu cầu response trong 5 giây
    return {"status": "ok"}


def _verify_shopee_signature(body: bytes, signature: str) -> bool:
    if not signature:
        return False
    expected = hmac.new(
        settings.SHOPEE_PARTNER_KEY.encode(),
        body,
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)
```

---

## Outbox Service

```python
# src/services/outbox_service.py

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_async_db_context
from src.core.redis import get_redis
from src.models.outbox import WebhookOutbox

logger = structlog.get_logger(__name__)

# Queue names mapping
QUEUE_MAP = {
    ("shopee", "order"):     "queue:orders.created.normal",
    ("shopee", "logistics"): "queue:shipment.confirm",
    ("shopee", "stock"):     "queue:stock.sync",
    ("lazada", "order"):     "queue:orders.created.normal",
    ("tiktok", "order"):     "queue:orders.created.normal",
}

# Retry delays (giây)
RETRY_DELAYS = [60, 300, 1800, 7200, 14400]  # 1m, 5m, 30m, 2h, 4h


class OutboxService:

    @classmethod
    async def try_publish_immediately(cls, outbox_id: int) -> bool:
        """
        Gọi ngay sau khi lưu outbox (background task).
        Thử push vào Redis queue. Nếu Redis down → relay job sẽ xử lý sau.
        """
        async with get_async_db_context() as db:
            entry = await db.get(WebhookOutbox, outbox_id)
            if not entry or entry.status != "pending":
                return False
            return await cls._publish_entry(db, entry)

    @classmethod
    async def relay_pending(cls, batch_size: int = 50) -> dict:
        """
        Relay job: đọc các entry pending/failed từ DB, push vào Redis.
        Chạy bởi Celery Beat mỗi 30 giây.
        """
        stats = {"published": 0, "failed": 0, "skipped": 0}

        async with get_async_db_context() as db:
            now = datetime.now(timezone.utc)

            # Lấy các entry cần xử lý
            result = await db.execute(
                select(WebhookOutbox)
                .where(
                    WebhookOutbox.status.in_(["pending", "failed"]),
                    WebhookOutbox.process_after <= now,
                    WebhookOutbox.retry_count < WebhookOutbox.max_retries,
                )
                .order_by(WebhookOutbox.created_at.asc())
                .limit(batch_size)
                .with_for_update(skip_locked=True)  # Tránh race condition
            )
            entries = result.scalars().all()

            for entry in entries:
                # Mark as processing (optimistic lock)
                entry.status = "processing"
                await db.flush()

                success = await cls._publish_entry(db, entry)
                if success:
                    stats["published"] += 1
                else:
                    stats["failed"] += 1

            await db.commit()

        logger.info("outbox_relay_completed", **stats)
        return stats

    @classmethod
    async def _publish_entry(cls, db: AsyncSession, entry: WebhookOutbox) -> bool:
        """Core logic: push entry vào Redis queue."""
        queue_name = QUEUE_MAP.get((entry.platform, entry.event_type))
        if not queue_name:
            # Event type không được handle → skip
            entry.status    = "dead_letter"
            entry.last_error = f"No queue mapping for {entry.platform}/{entry.event_type}"
            await db.flush()
            return False

        try:
            redis = await get_redis()

            # Push task vào Celery queue
            # Dùng Celery's task_id để đảm bảo idempotency
            task_payload = {
                "outbox_id":          entry.id,
                "platform":           entry.platform,
                "event_type":         entry.event_type,
                "platform_order_id":  entry.platform_order_id,
                "payload":            entry.payload,
            }

            # Gửi vào Celery queue (dùng celery's apply_async)
            from src.workers.order_worker import process_webhook_event
            task = process_webhook_event.apply_async(
                kwargs=task_payload,
                queue=queue_name,
                task_id=f"outbox-{entry.id}",  # Idempotent task ID
            )

            # Update outbox entry
            entry.status       = "published"
            entry.published_at = datetime.now(timezone.utc)
            await db.flush()

            logger.info("outbox_entry_published",
                        outbox_id=entry.id,
                        task_id=task.id,
                        queue=queue_name)
            return True

        except Exception as e:
            # Redis down hoặc lỗi khác
            entry.retry_count += 1
            entry.last_error   = str(e)

            if entry.retry_count >= entry.max_retries:
                entry.status = "dead_letter"
                logger.error("outbox_entry_dead_letter",
                             outbox_id=entry.id, error=str(e))
                await cls._send_dead_letter_alert(entry)
            else:
                entry.status = "failed"
                delay_seconds = RETRY_DELAYS[min(
                    entry.retry_count - 1, len(RETRY_DELAYS) - 1
                )]
                entry.process_after = datetime.now(timezone.utc) + timedelta(
                    seconds=delay_seconds
                )
                logger.warning("outbox_entry_retry_scheduled",
                               outbox_id=entry.id,
                               retry_count=entry.retry_count,
                               next_attempt_in=delay_seconds)

            await db.flush()
            return False

    @staticmethod
    async def _send_dead_letter_alert(entry: WebhookOutbox):
        """Gửi Slack alert khi entry vào dead letter."""
        # Import ở đây để tránh circular import
        from src.services.alert_service import AlertService
        await AlertService.send_dead_letter_alert(
            outbox_id=entry.id,
            platform=entry.platform,
            order_id=entry.platform_order_id,
            error=entry.last_error,
        )

    @classmethod
    async def get_stats(cls) -> dict:
        """Lấy thống kê outbox cho Admin UI và monitoring."""
        async with get_async_db_context() as db:
            from sqlalchemy import func, case
            result = await db.execute(
                select(
                    WebhookOutbox.status,
                    func.count().label("count")
                ).group_by(WebhookOutbox.status)
            )
            rows = result.all()
            return {row.status: row.count for row in rows}

    @classmethod
    async def manual_retry(cls, outbox_id: int) -> bool:
        """Manual retry từ Admin UI."""
        async with get_async_db_context() as db:
            entry = await db.get(WebhookOutbox, outbox_id)
            if not entry:
                return False

            entry.status        = "pending"
            entry.process_after = datetime.now(timezone.utc)
            entry.retry_count   = 0
            entry.last_error    = None
            await db.commit()

            return await cls.try_publish_immediately(outbox_id)
```

---

## Celery Relay Task (Scheduled)

```python
# src/workers/scheduled.py

from celery import shared_task
from celery.schedules import crontab
import structlog

logger = structlog.get_logger(__name__)


@shared_task(name="outbox.relay_pending")
def relay_outbox_pending():
    """Chạy mỗi 30 giây - đọc outbox DB và push vào Redis queue."""
    import asyncio
    from src.services.outbox_service import OutboxService

    loop = asyncio.new_event_loop()
    stats = loop.run_until_complete(OutboxService.relay_pending(batch_size=100))
    loop.close()

    logger.info("outbox_relay_task_done", **stats)
    return stats


@shared_task(name="outbox.cleanup_old_entries")
def cleanup_old_outbox_entries():
    """Chạy mỗi ngày - xóa entries đã published > 7 ngày."""
    from datetime import datetime, timedelta, timezone
    from src.core.database import get_sync_db
    from src.models.outbox import WebhookOutbox

    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    with get_sync_db() as db:
        deleted = db.query(WebhookOutbox).filter(
            WebhookOutbox.status == "published",
            WebhookOutbox.published_at < cutoff
        ).delete()
        db.commit()

    logger.info("outbox_cleanup_done", deleted_count=deleted)
    return {"deleted": deleted}


# Celery Beat schedule
CELERY_BEAT_SCHEDULE = {
    "relay-outbox-every-30s": {
        "task":     "outbox.relay_pending",
        "schedule": 30.0,  # mỗi 30 giây
    },
    "cleanup-outbox-daily": {
        "task":     "outbox.cleanup_old_entries",
        "schedule": crontab(hour=3, minute=0),  # 3:00 AM mỗi ngày
    },
}
```
