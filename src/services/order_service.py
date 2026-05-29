"""Order service — idempotent sync to Odoo."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import select, text

from src.core.database import get_async_db_context
from src.core.logging import get_logger
from src.models.order_mapping import OrderMapping
from src.odoo.client import OdooClient

if TYPE_CHECKING:
    from src.schemas.unified import OrderStatus, UnifiedOrder

logger = get_logger(__name__)

# How long a row marked `syncing` is treated as actively held by another
# worker. Beyond this, a fresh arrival reclaims it (the previous worker
# is presumed dead). Should comfortably exceed worst-case Odoo XML-RPC
# latency + retries.
_SYNCING_STALE_AFTER = timedelta(minutes=5)


class OrderService:
    def __init__(self, odoo: OdooClient | None = None) -> None:
        self.odoo = odoo or OdooClient()

    async def sync(self, order: UnifiedOrder) -> dict:
        # Phase A — short transaction under advisory lock: claim or skip.
        # We must NOT keep this transaction (or the lock) open across the
        # subsequent Odoo XML-RPC calls, or an Odoo outage will pin DB
        # connections and stall every concurrent sync for the same shop.
        platform = order.platform.value
        platform_order_id = order.platform_order_id
        lock_key = f"{platform}:{platform_order_id}"

        async with get_async_db_context() as db:
            await db.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
                {"k": lock_key},
            )
            existing = (await db.execute(
                select(OrderMapping).where(
                    OrderMapping.platform == platform,
                    OrderMapping.platform_order_id == platform_order_id,
                ),
            )).scalar_one_or_none()

            if existing and existing.status == "success":
                await db.commit()  # release advisory lock + close txn
                logger.info("order_sync_skipped_duplicate",
                            platform=platform, platform_order_id=platform_order_id)
                return {"status": "skipped", "reason": "duplicate"}

            if existing and existing.status == "syncing":
                age = datetime.now(UTC) - existing.updated_at
                if age < _SYNCING_STALE_AFTER:
                    await db.commit()  # release advisory lock + close txn
                    logger.info("order_sync_skipped_in_flight",
                                platform=platform, platform_order_id=platform_order_id,
                                age_seconds=age.total_seconds())
                    return {"status": "skipped", "reason": "in_flight"}

            mapping = existing or OrderMapping(
                platform=platform,
                platform_order_id=platform_order_id,
                platform_order_sn=order.platform_order_sn,
                status="syncing",
            )
            if existing is None:
                db.add(mapping)
            else:
                # Reclaim a stale `syncing` row (existing code path enters
                # here only after age >= _SYNCING_STALE_AFTER). Force a
                # touched updated_at so a duplicate webhook arriving while
                # we hold this advisory lock can't see the row as stale
                # too. Setting status to the same value would NOT trigger
                # an UPDATE in SQLAlchemy.
                mapping.status = "syncing"
                mapping.retry_count = (mapping.retry_count or 0) + 1
                mapping.updated_at = datetime.now(UTC)
            await db.flush()
            mapping_id = mapping.id
            await db.commit()

        # Phase B — Odoo network calls happen OUTSIDE any DB lock/txn.
        try:
            odoo_existing = await self.odoo.check_order_exists(
                platform, platform_order_id,
            )
            if odoo_existing:
                async with get_async_db_context() as db:
                    row = await db.get(OrderMapping, mapping_id)
                    if row is not None:
                        row.odoo_order_id = odoo_existing
                        row.status = "success"
                        row.last_error = None
                    await db.commit()
                return {"status": "skipped", "reason": "exists_in_odoo"}

            odoo_id, odoo_name = await self.odoo.create_sale_order(order)
        except Exception as e:
            async with get_async_db_context() as db:
                row = await db.get(OrderMapping, mapping_id)
                if row is not None:
                    row.status = "failed"
                    row.retry_count += 1
                    row.last_error = str(e)
                    await db.commit()
            raise

        # Phase C — record success.
        async with get_async_db_context() as db:
            row = await db.get(OrderMapping, mapping_id)
            if row is not None:
                row.odoo_order_id = odoo_id
                row.odoo_order_name = odoo_name
                row.status = "success"
                row.last_error = None
                await db.commit()
        logger.info("order_synced",
                    platform=platform,
                    platform_order_id=platform_order_id,
                    odoo_order_id=odoo_id)
        return {"status": "success", "odoo_order_id": odoo_id, "odoo_order_name": odoo_name}

    async def update_status(self, platform: str, platform_order_id: str,
                            new_status: OrderStatus) -> None:
        async with get_async_db_context() as db:
            mapping = (await db.execute(
                select(OrderMapping).where(
                    OrderMapping.platform == platform,
                    OrderMapping.platform_order_id == platform_order_id,
                ),
            )).scalar_one_or_none()
            if not mapping or not mapping.odoo_order_id:
                return
            # Simplified: caller decides status field on Odoo side
            await self.odoo.write("sale.order", [mapping.odoo_order_id],
                                  {"x_sync_status": "synced"})
