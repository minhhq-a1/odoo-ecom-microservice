"""Admin web UI for platform config, product mapping, stock allocation, Shopee OAuth wizard."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import desc, select

from src.api.csrf_helper import set_csrf_cookie
from src.api.dependencies import get_async_db, require_admin_token, set_admin_cookie, verify_csrf
from src.connectors.shopee.oauth import auth_status as shopee_auth_status
from src.connectors.shopee.oauth import build_auth_url, issue_oauth_state
from src.core.config import settings
from src.core.logging import get_logger
from src.models.audit_log import AuditLog
from src.models.platform_config import PlatformConfig
from src.models.product_mapping import ProductBundleComponent, ProductMapping
from src.models.stock_config import StockAllocationConfig

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/admin/config", tags=["admin", "config"])
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


# ───────────── Platform Config ─────────────


@router.get("/platforms", response_class=HTMLResponse)
async def platforms_list(
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    _: str = Depends(require_admin_token),
    flash: str | None = Query(default=None),
) -> HTMLResponse:
    rows = (
        (await db.execute(select(PlatformConfig).order_by(PlatformConfig.platform))).scalars().all()
    )
    resp = templates.TemplateResponse(
        "admin/platforms.html",
        {
            "request": request,
            "rows": rows,
            "flash": flash,
            "csrf_token": "",
        },
    )
    set_admin_cookie(resp)
    csrf_token = set_csrf_cookie(resp)
    resp.context["csrf_token"] = csrf_token
    return resp


@router.get("/platforms/new", response_class=HTMLResponse)
async def platforms_new_form(
    request: Request,
    _: str = Depends(require_admin_token),
) -> HTMLResponse:
    resp = templates.TemplateResponse(
        "admin/platform_form.html",
        {
            "request": request,
            "row": None,
            "is_new": True,
            "csrf_token": "",
        },
    )
    set_admin_cookie(resp)
    csrf_token = set_csrf_cookie(resp)
    resp.context["csrf_token"] = csrf_token
    return resp


@router.get("/platforms/{platform}", response_class=HTMLResponse)
async def platforms_edit_form(
    platform: str,
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    _: str = Depends(require_admin_token),
) -> HTMLResponse:
    row = (
        await db.execute(
            select(PlatformConfig).where(PlatformConfig.platform == platform),
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404)
    resp = templates.TemplateResponse(
        "admin/platform_form.html",
        {
            "request": request,
            "row": row,
            "is_new": False,
            "csrf_token": "",
        },
    )
    set_admin_cookie(resp)
    csrf_token = set_csrf_cookie(resp)
    resp.context["csrf_token"] = csrf_token
    return resp


@router.post("/platforms")
async def platforms_save(
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    _auth: str = Depends(require_admin_token),
    _csrf: None = Depends(verify_csrf),
    platform: str = Form(...),
    shop_id: str = Form(default=""),
    is_active: str = Form(default=""),
    dry_run: str = Form(default=""),
    price_master: str = Form(default="platform"),
) -> RedirectResponse:
    if platform not in ("shopee", "lazada", "tiktok"):
        raise HTTPException(400, "Invalid platform")
    if price_master not in ("platform", "odoo"):
        raise HTTPException(400, "Invalid price_master")

    row = (
        await db.execute(
            select(PlatformConfig).where(PlatformConfig.platform == platform),
        )
    ).scalar_one_or_none()
    is_new = row is None
    if is_new:
        row = PlatformConfig(platform=platform, credentials={})
        db.add(row)
    row.shop_id = shop_id or None
    row.is_active = is_active == "on"
    row.dry_run = dry_run == "on"
    row.price_master = price_master
    await _audit(
        db,
        request,
        "config_platform_save",
        target=f"platform:{platform}",
        payload={
            "is_active": row.is_active,
            "dry_run": row.dry_run,
            "price_master": price_master,
            "is_new": is_new,
        },
    )
    await db.commit()
    return RedirectResponse(url=f"/admin/config/platforms?flash=Saved+{platform}", status_code=303)


@router.post("/platforms/{platform}/delete")
async def platforms_delete(
    platform: str,
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    _auth: str = Depends(require_admin_token),
    _csrf: None = Depends(verify_csrf),
) -> RedirectResponse:
    row = (
        await db.execute(
            select(PlatformConfig).where(PlatformConfig.platform == platform),
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404)
    await db.delete(row)
    await _audit(db, request, "config_platform_delete", target=f"platform:{platform}")
    await db.commit()
    return RedirectResponse(url="/admin/config/platforms?flash=Deleted", status_code=303)


# ───────────── Shopee OAuth Wizard ─────────────


@router.get("/shopee", response_class=HTMLResponse)
async def shopee_setup(
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    _: str = Depends(require_admin_token),
    flash: str | None = Query(default=None),
) -> HTMLResponse:
    shop_id = settings.SHOPEE_SHOP_ID
    status = await shopee_auth_status(shop_id)

    redirect_uri = str(request.url_for("oauth_callback"))
    try:
        state = await issue_oauth_state()
        auth_link = build_auth_url(redirect_uri, state=state).url
        auth_error = None
    except ValueError as e:
        auth_link = None
        auth_error = str(e)

    cfg = (
        await db.execute(
            select(PlatformConfig).where(PlatformConfig.platform == "shopee"),
        )
    ).scalar_one_or_none()

    resp = templates.TemplateResponse(
        "admin/shopee_setup.html",
        {
            "request": request,
            "status": status,
            "auth_link": auth_link,
            "auth_error": auth_error,
            "redirect_uri": redirect_uri,
            "partner_id": settings.SHOPEE_PARTNER_ID,
            "shop_id": shop_id,
            "sandbox": settings.SHOPEE_IS_SANDBOX,
            "cfg": cfg,
            "flash": flash,
            "csrf_token": "",
        },
    )
    set_admin_cookie(resp)
    csrf_token = set_csrf_cookie(resp)
    resp.context["csrf_token"] = csrf_token
    return resp


def _set_admin_cookie(resp: HTMLResponse) -> None:
    """Backwards-compatible wrapper; new code should import set_admin_cookie."""
    set_admin_cookie(resp)


# ───────────── Product Mapping ─────────────


@router.get("/products", response_class=HTMLResponse)
async def products_list(
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    _: str = Depends(require_admin_token),
    platform: str | None = Query(default=None),
    search: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    flash: str | None = Query(default=None),
) -> HTMLResponse:
    page_size = 50
    q = select(ProductMapping).order_by(desc(ProductMapping.created_at))
    if platform:
        q = q.where(ProductMapping.platform == platform)
    if search:
        like = f"%{search}%"
        q = q.where(
            (ProductMapping.odoo_sku.ilike(like)) | (ProductMapping.platform_sku_id.ilike(like)),
        )
    rows = (await db.execute(q.limit(page_size).offset((page - 1) * page_size))).scalars().all()
    resp = templates.TemplateResponse(
        "admin/products.html",
        {
            "request": request,
            "rows": rows,
            "platform": platform,
            "search": search,
            "page": page,
            "flash": flash,
            "csrf_token": "",
        },
    )
    set_admin_cookie(resp)
    csrf_token = set_csrf_cookie(resp)
    resp.context["csrf_token"] = csrf_token
    return resp


@router.get("/products/new", response_class=HTMLResponse)
async def products_new_form(
    request: Request,
    _: str = Depends(require_admin_token),
) -> HTMLResponse:
    resp = templates.TemplateResponse(
        "admin/product_form.html",
        {
            "request": request,
            "row": None,
            "components": [],
            "is_new": True,
            "csrf_token": "",
        },
    )
    set_admin_cookie(resp)
    csrf_token = set_csrf_cookie(resp)
    resp.context["csrf_token"] = csrf_token
    return resp


@router.get("/products/{mapping_id}", response_class=HTMLResponse)
async def products_edit_form(
    mapping_id: int,
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    _: str = Depends(require_admin_token),
) -> HTMLResponse:
    row = await db.get(ProductMapping, mapping_id)
    if row is None:
        raise HTTPException(404)
    comps = (
        (
            await db.execute(
                select(ProductBundleComponent).where(
                    ProductBundleComponent.mapping_id == mapping_id
                ),
            )
        )
        .scalars()
        .all()
    )
    resp = templates.TemplateResponse(
        "admin/product_form.html",
        {
            "request": request,
            "row": row,
            "components": comps,
            "is_new": False,
            "csrf_token": "",
        },
    )
    set_admin_cookie(resp)
    csrf_token = set_csrf_cookie(resp)
    resp.context["csrf_token"] = csrf_token
    return resp


@router.post("/products")
async def products_save(
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    _auth: str = Depends(require_admin_token),
    _csrf: None = Depends(verify_csrf),
    mapping_id: str = Form(default=""),
    platform: str = Form(...),
    platform_product_id: str = Form(...),
    platform_sku_id: str = Form(default=""),
    odoo_product_id: int = Form(...),
    odoo_sku: str = Form(...),
    mapping_type: str = Form(default="simple"),
    is_active: str = Form(default=""),
    components_sku: list[str] = Form(default=[]),
    components_qty: list[str] = Form(default=[]),
) -> RedirectResponse:
    if mapping_type not in ("simple", "bundle"):
        raise HTTPException(400, "Invalid mapping_type")
    if platform not in ("shopee", "lazada", "tiktok"):
        raise HTTPException(400, "Invalid platform")

    if mapping_id:
        row = await db.get(ProductMapping, int(mapping_id))
        if row is None:
            raise HTTPException(404)
        old_platform_sku = row.platform_sku_id
        old_platform = row.platform
    else:
        row = ProductMapping()
        db.add(row)
        old_platform_sku = None
        old_platform = None

    row.platform = platform
    row.platform_product_id = platform_product_id
    row.platform_sku_id = platform_sku_id or None
    row.odoo_product_id = odoo_product_id
    row.odoo_sku = odoo_sku
    row.mapping_type = mapping_type
    row.is_active = is_active == "on"
    await db.flush()

    # Reset bundle components
    existing = (
        (
            await db.execute(
                select(ProductBundleComponent).where(ProductBundleComponent.mapping_id == row.id),
            )
        )
        .scalars()
        .all()
    )
    for c in existing:
        await db.delete(c)

    if mapping_type == "bundle":
        for sku, qty in zip(components_sku, components_qty, strict=False):
            sku = sku.strip()
            if not sku:
                continue
            try:
                q = max(1, int(qty))
            except ValueError:
                q = 1
            db.add(ProductBundleComponent(mapping_id=row.id, odoo_sku=sku, quantity=q))

    await _audit(
        db,
        request,
        "config_product_save",
        target=f"product_mapping:{row.id}",
        payload={"platform": platform, "odoo_sku": odoo_sku, "mapping_type": mapping_type},
    )
    await db.commit()

    # Invalidate Redis caches: old + new platform_sku entries, bundle components
    try:
        from src.core.redis import get_redis
        from src.services.mapping_service import MappingService

        r = await get_redis()
        if old_platform_sku and (
            old_platform != platform or old_platform_sku != row.platform_sku_id
        ):
            await MappingService.invalidate(old_platform, old_platform_sku)
        if row.platform_sku_id:
            await MappingService.invalidate(platform, row.platform_sku_id)
        await r.delete(f"mapping:bundle:{row.id}")
    except Exception as e:
        logger.warning("mapping_cache_invalidate_failed", error=str(e))

    return RedirectResponse(url="/admin/config/products?flash=Saved", status_code=303)


@router.post("/products/{mapping_id}/delete")
async def products_delete(
    mapping_id: int,
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    _auth: str = Depends(require_admin_token),
    _csrf: None = Depends(verify_csrf),
) -> RedirectResponse:
    row = await db.get(ProductMapping, mapping_id)
    if row is None:
        raise HTTPException(404)
    old_platform = row.platform
    old_platform_sku = row.platform_sku_id
    await db.delete(row)
    await _audit(db, request, "config_product_delete", target=f"product_mapping:{mapping_id}")
    await db.commit()

    try:
        from src.core.redis import get_redis
        from src.services.mapping_service import MappingService

        r = await get_redis()
        if old_platform_sku:
            await MappingService.invalidate(old_platform, old_platform_sku)
        await r.delete(f"mapping:bundle:{mapping_id}")
    except Exception as e:
        logger.warning("mapping_cache_invalidate_failed", error=str(e))

    return RedirectResponse(url="/admin/config/products?flash=Deleted", status_code=303)


# ───────────── Stock Allocation ─────────────


@router.get("/stock", response_class=HTMLResponse)
async def stock_list(
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    _: str = Depends(require_admin_token),
    flash: str | None = Query(default=None),
) -> HTMLResponse:
    rows = (
        (
            await db.execute(
                select(StockAllocationConfig).order_by(StockAllocationConfig.odoo_sku),
            )
        )
        .scalars()
        .all()
    )
    resp = templates.TemplateResponse(
        "admin/stock_config.html",
        {
            "request": request,
            "rows": rows,
            "flash": flash,
            "csrf_token": "",
        },
    )
    set_admin_cookie(resp)
    csrf_token = set_csrf_cookie(resp)
    resp.context["csrf_token"] = csrf_token
    return resp


@router.post("/stock")
async def stock_save(
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    _auth: str = Depends(require_admin_token),
    _csrf: None = Depends(verify_csrf),
    cfg_id: str = Form(default=""),
    odoo_sku: str = Form(...),
    platform: str = Form(...),
    allocation_pct: float = Form(...),
    buffer_pct: float = Form(default=10.0),
    is_active: str = Form(default=""),
) -> RedirectResponse:
    if not (0 <= allocation_pct <= 100) or not (0 <= buffer_pct <= 100):
        raise HTTPException(400, "Percentages must be 0–100")
    if platform not in ("shopee", "lazada", "tiktok"):
        raise HTTPException(400, "Invalid platform")

    if cfg_id:
        row = await db.get(StockAllocationConfig, int(cfg_id))
        if row is None:
            raise HTTPException(404)
    else:
        row = StockAllocationConfig()
        db.add(row)

    row.odoo_sku = odoo_sku
    row.platform = platform
    row.allocation_pct = allocation_pct
    row.buffer_pct = buffer_pct
    row.is_active = is_active == "on"
    await _audit(
        db,
        request,
        "config_stock_save",
        target=f"stock_config:{row.odoo_sku}/{platform}",
        payload={"allocation_pct": allocation_pct, "buffer_pct": buffer_pct},
    )
    await db.commit()
    return RedirectResponse(url="/admin/config/stock?flash=Saved", status_code=303)


@router.post("/stock/{cfg_id}/delete")
async def stock_delete(
    cfg_id: int,
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    _auth: str = Depends(require_admin_token),
    _csrf: None = Depends(verify_csrf),
) -> RedirectResponse:
    row = await db.get(StockAllocationConfig, cfg_id)
    if row is None:
        raise HTTPException(404)
    await db.delete(row)
    await _audit(db, request, "config_stock_delete", target=f"stock_config:{cfg_id}")
    await db.commit()
    return RedirectResponse(url="/admin/config/stock?flash=Deleted", status_code=303)
