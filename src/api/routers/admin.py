"""Admin UI — minimal scaffold delegating heavy lifting to ADMIN_UI.md doc."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import desc, func, select

from src.api.csrf_helper import set_csrf_cookie
from src.api.dependencies import get_async_db, require_admin_token, set_admin_cookie, verify_csrf
from src.core.config import settings
from src.core.logging import get_logger
from src.models.audit_log import AuditLog
from src.models.order_mapping import OrderMapping

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/admin", tags=["admin"])
templates = Jinja2Templates(directory="src/api/templates")
logger = get_logger(__name__)


async def _audit(
    db: AsyncSession,
    request: Request,
    action: str,
    target: str | None = None,
    payload: dict | None = None,
) -> None:
    db.add(
        AuditLog(
            actor="admin",
            action=action,
            target=target,
            ip=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
            payload=payload,
            success=True,
        )
    )
    await db.commit()


@router.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    _: str = Depends(require_admin_token),
) -> HTMLResponse:
    since = datetime.now(UTC) - timedelta(hours=24)
    rows = (
        await db.execute(
            select(OrderMapping.status, func.count())
            .where(OrderMapping.created_at >= since)
            .group_by(OrderMapping.status),
        )
    ).all()
    order_stats = {r[0]: r[1] for r in rows}

    from src.services.outbox_service import OutboxService

    outbox_stats = await OutboxService.get_stats()

    # Application Redis DB holds caches/nonces, not Celery queues. Read
    # broker queue depths from the broker DB directly so the dashboard
    # reflects what Celery sees.
    from redis.asyncio import Redis as AsyncRedis

    broker = AsyncRedis.from_url(str(settings.CELERY_BROKER_URL), decode_responses=True)
    try:
        queue_depths = {
            "high": await broker.llen("orders.created.high"),
            "normal": await broker.llen("orders.created.normal"),
            "stock": await broker.llen("stock.sync"),
        }
    finally:
        await broker.aclose()

    recent_failures = (
        (
            await db.execute(
                select(OrderMapping)
                .where(OrderMapping.status.in_(["failed", "dead_letter"]))
                .order_by(desc(OrderMapping.updated_at))
                .limit(10),
            )
        )
        .scalars()
        .all()
    )

    resp = templates.TemplateResponse(
        "admin/dashboard.html",
        {
            "request": request,
            "order_stats": order_stats,
            "outbox_stats": outbox_stats,
            "queue_depths": queue_depths,
            "recent_failures": recent_failures,
            "now": datetime.now(UTC),
            "settings": settings,
            "csrf_token": "",  # Will be set by set_csrf_cookie
        },
    )
    set_admin_cookie(resp)
    csrf_token = set_csrf_cookie(resp)
    # Update template context with actual token
    resp.context["csrf_token"] = csrf_token
    return resp


@router.post("/orders/{order_id}/retry")
async def retry_order(
    order_id: int,
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    _auth: str = Depends(require_admin_token),
    _csrf: None = Depends(verify_csrf),
) -> RedirectResponse:
    mapping = await db.get(OrderMapping, order_id)
    if not mapping:
        raise HTTPException(status_code=404)
    if mapping.status not in ("failed", "dead_letter"):
        raise HTTPException(status_code=400, detail=f"status={mapping.status}")
    mapping.status = "pending"
    mapping.retry_count = 0
    mapping.last_error = None
    # Audit row is committed in the same transaction as the status reset,
    # BEFORE enqueueing the Celery task. If commit fails we return 500
    # and the task was never dispatched (consistent state). If commit
    # succeeds and the subsequent dispatch raises, the audit row still
    # records operator intent and the admin can re-trigger the retry.
    db.add(
        AuditLog(
            actor="admin",
            action="retry_order",
            target=f"order_mapping:{order_id}",
            ip=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
            payload=None,
            success=True,
        )
    )
    await db.commit()

    from src.workers.order_worker import sync_order_to_odoo

    sync_order_to_odoo.apply_async(
        kwargs={"order_mapping_id": order_id},
        queue="orders.created.normal",
    )
    return RedirectResponse(url="/admin/", status_code=303)
