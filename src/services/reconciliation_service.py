"""Reconciliation service — nightly diff platform vs Odoo + field drift check."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select, text

from src.core.config import settings
from src.core.database import get_async_db_context
from src.core.logging import get_logger
from src.models.order_mapping import OrderMapping
from src.models.reconciliation_log import ReconciliationLog
from src.odoo.client import OdooClient
from src.schemas.unified import OrderStatus, UnifiedOrder
from src.services.alert_service import AlertService
from src.services.order_service import OrderService

logger = get_logger(__name__)


AUTO_FIX_STATUSES = (OrderStatus.CONFIRMED, OrderStatus.PROCESSING)
DRIFT_TOTAL_TOLERANCE_VND = Decimal("1000")
_IN_CHUNK_SIZE = 500


@dataclass
class Drift:
    platform: str
    platform_order_id: str
    field: str
    platform_value: object
    odoo_value: object


@dataclass
class ReconciliationResult:
    platform: str
    run_date: date
    orders_checked: int = 0
    orders_matched: int = 0
    orders_missing: int = 0
    orders_extra: int = 0
    auto_fixed: int = 0
    needs_review: int = 0
    drifts: list[Drift] = field(default_factory=list)
    missing_details: list[dict] = field(default_factory=list)


class ReconciliationService:
    def __init__(
        self, odoo: OdooClient | None = None,
        order_service: OrderService | None = None,
    ) -> None:
        self.odoo = odoo or OdooClient()
        self.order_service = order_service or OrderService(self.odoo)

    async def run_for_platform(
        self, platform: str, run_date: date,
    ) -> ReconciliationResult:
        logger.info("reconciliation_start", platform=platform, run_date=run_date.isoformat())
        result = ReconciliationResult(platform=platform, run_date=run_date)

        if settings.MIDDLEWARE_DRY_RUN:
            # In dry-run no OrderMapping rows are written by the worker
            # (process_webhook_event returns early), so every platform order
            # would appear missing and reconciliation would alert on the entire
            # day. Codex round 2 P2-B — skip with a sentinel log instead.
            logger.info(
                "reconciliation_skipped_dry_run",
                platform=platform, run_date=run_date.isoformat(),
            )
            await self._persist(result)
            return result

        platform_orders = await self._fetch_platform_orders(platform, run_date)
        result.orders_checked = len(platform_orders)
        if not platform_orders:
            await self._persist(result)
            return result

        platform_ids = {o.platform_order_id for o in platform_orders}

        # Codex round 3 P2-γ: open a single REPEATABLE READ snapshot covering
        # both the synced-ids lookup and the field-drift mapping load so a
        # concurrent worker write between the two queries can't make us mark
        # an order missing AND drifted at the same time.
        async with get_async_db_context() as db:
            await db.execute(
                text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"),
            )
            synced_ids = await self._lookup_synced_ids(db, platform, platform_ids)
            day_synced_ids = await self._lookup_synced_ids_for_date(
                db, platform, run_date,
            )
            field_drift_mappings = await self._load_drift_mappings(
                db, platform, {o.platform_order_id for o in platform_orders
                               if o.platform_order_id in synced_ids},
            )

        missing = [o for o in platform_orders if o.platform_order_id not in synced_ids]
        result.orders_missing = len(missing)
        result.orders_matched = result.orders_checked - len(missing)
        result.orders_extra = len(day_synced_ids - platform_ids)

        for order in missing:
            ok = await self._try_auto_fix(order)
            if ok:
                result.auto_fixed += 1
            else:
                result.needs_review += 1
                result.missing_details.append({
                    "platform_order_id": order.platform_order_id,
                    "status": order.status.value,
                    "total_amount": str(order.total_amount),
                    "reason": "auto_fix_failed_or_unsafe_status",
                })

        drifts = await self._check_field_drift(platform, platform_orders, synced_ids,
                                                field_drift_mappings)
        result.drifts = drifts
        if drifts:
            result.needs_review += sum(1 for d in drifts if d.field != "status_auto_fixed")

        await self._persist(result)

        if result.needs_review > 0 or result.drifts:
            await AlertService.send_reconciliation_alert(
                platform, result.missing_details + [d.__dict__ for d in drifts],
            )

        logger.info(
            "reconciliation_done", platform=platform,
            checked=result.orders_checked, missing=result.orders_missing,
            auto_fixed=result.auto_fixed, needs_review=result.needs_review,
            drifts=len(result.drifts),
        )
        return result

    async def run_all_platforms(self, run_date: date | None = None) -> list[ReconciliationResult]:
        target = run_date or (datetime.now(UTC).date() - timedelta(days=1))
        platforms = ["shopee"]
        results: list[ReconciliationResult] = []
        for p in platforms:
            try:
                results.append(await self.run_for_platform(p, target))
            except Exception as e:
                logger.exception("reconciliation_platform_failed", platform=p, error=str(e))
                await AlertService.send_reconciliation_alert(
                    p, [{"error": str(e), "phase": "run_for_platform"}],
                )
        return results

    async def _fetch_platform_orders(
        self, platform: str, run_date: date,
    ) -> list[UnifiedOrder]:
        if platform != "shopee":
            return []
        from src.connectors.shopee import ShopeeConnector
        async with ShopeeConnector() as conn:
            return [o async for o in conn.get_orders_by_date(run_date)]

    async def _lookup_synced_ids(
        self, db, platform: str, platform_ids: set[str],
    ) -> set[str]:
        if not platform_ids:
            return set()
        ids = list(platform_ids)
        found: set[str] = set()
        for i in range(0, len(ids), _IN_CHUNK_SIZE):
            chunk = ids[i:i + _IN_CHUNK_SIZE]
            rows = (await db.execute(
                select(OrderMapping.platform_order_id).where(
                    OrderMapping.platform == platform,
                    OrderMapping.platform_order_id.in_(chunk),
                    OrderMapping.status == "success",
                ),
            )).scalars().all()
            found.update(rows)
        return found

    async def _lookup_synced_ids_for_date(
        self, db, platform: str, run_date: date,
    ) -> set[str]:
        """Return ALL platform_order_ids synced (status=success) whose
        created_at falls within run_date [UTC]. Used to compute the
        'extra' set: orders middleware has but platform did not return."""
        day_start = datetime.combine(run_date, datetime.min.time(), tzinfo=UTC)
        day_end = day_start + timedelta(days=1)
        rows = (await db.execute(
            select(OrderMapping.platform_order_id).where(
                OrderMapping.platform == platform,
                OrderMapping.status == "success",
                OrderMapping.created_at >= day_start,
                OrderMapping.created_at < day_end,
            ),
        )).scalars().all()
        return set(rows)

    async def _load_drift_mappings(
        self, db, platform: str, platform_ids: set[str],
    ) -> list:
        if not platform_ids:
            return []
        ids = list(platform_ids)
        out: list = []
        for i in range(0, len(ids), _IN_CHUNK_SIZE):
            chunk = ids[i:i + _IN_CHUNK_SIZE]
            rows = (await db.execute(
                select(OrderMapping).where(
                    OrderMapping.platform == platform,
                    OrderMapping.platform_order_id.in_(chunk),
                    OrderMapping.status == "success",
                ),
            )).scalars().all()
            out.extend(rows)
        return out

    async def _try_auto_fix(self, order: UnifiedOrder) -> bool:
        if order.status not in AUTO_FIX_STATUSES:
            return False
        if settings.MIDDLEWARE_DRY_RUN:
            logger.info(
                "reconciliation_dry_run_skip_fix",
                platform_order_id=order.platform_order_id, status=order.status.value,
            )
            return False
        try:
            outcome = await self.order_service.sync(order)
            return outcome.get("status") == "success"
        except Exception as e:
            logger.warning(
                "reconciliation_auto_fix_failed",
                platform_order_id=order.platform_order_id, error=str(e),
            )
            return False

    async def _check_field_drift(
        self, platform: str, platform_orders: list[UnifiedOrder],
        synced_ids: set[str], mappings: list,
    ) -> list[Drift]:
        drifts: list[Drift] = []
        synced = {o.platform_order_id: o for o in platform_orders
                  if o.platform_order_id in synced_ids}
        if not synced or not mappings:
            return drifts

        for mapping in mappings:
            if not mapping.odoo_order_id:
                continue
            try:
                rows = await self.odoo.search_read(
                    "sale.order", [["id", "=", mapping.odoo_order_id]],
                    ["amount_total", "state", "x_tracking_number"], limit=1,
                )
            except Exception as e:
                logger.warning("drift_odoo_read_failed",
                               odoo_id=mapping.odoo_order_id, error=str(e))
                continue
            if not rows:
                continue
            odoo_row = rows[0]
            pf = synced.get(mapping.platform_order_id)
            if pf is None:
                continue

            odoo_amount = Decimal(str(odoo_row.get("amount_total") or 0))
            if abs(pf.total_amount - odoo_amount) > DRIFT_TOTAL_TOLERANCE_VND:
                drifts.append(Drift(
                    platform=platform, platform_order_id=pf.platform_order_id,
                    field="total_amount",
                    platform_value=str(pf.total_amount), odoo_value=str(odoo_amount),
                ))

            pf_tracking = pf.logistics.tracking_number if pf.logistics else None
            odoo_tracking = odoo_row.get("x_tracking_number") or None
            if pf_tracking and pf_tracking != odoo_tracking:
                drifts.append(Drift(
                    platform=platform, platform_order_id=pf.platform_order_id,
                    field="tracking_number",
                    platform_value=pf_tracking, odoo_value=odoo_tracking,
                ))
        return drifts

    async def _persist(self, result: ReconciliationResult) -> None:
        async with get_async_db_context() as db:
            db.add(ReconciliationLog(
                platform=result.platform,
                run_date=result.run_date,
                orders_checked=result.orders_checked,
                orders_matched=result.orders_matched,
                orders_missing=result.orders_missing,
                orders_extra=result.orders_extra,
                auto_fixed=result.auto_fixed,
                needs_review=result.needs_review,
                details={
                    "missing": result.missing_details,
                    "drifts": [d.__dict__ for d in result.drifts],
                },
            ))
            await db.commit()
