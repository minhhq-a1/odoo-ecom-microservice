"""Shopee OAuth admin endpoints — token status + callback handler."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse

from src.api.dependencies import require_admin_token
from src.connectors.shopee.oauth import (
    auth_status,
    build_auth_url,
    consume_oauth_state,
    exchange_code_for_tokens,
    issue_oauth_state,
    persist_tokens,
)
from src.core.config import settings
from src.core.exceptions import ShopeeAuthError, ShopeeError
from src.core.logging import get_logger

router = APIRouter(prefix="/admin/shopee", tags=["admin", "shopee"])
logger = get_logger(__name__)


@router.get("/auth-status")
async def auth_status_endpoint(
    shop_id: str | None = Query(default=None),
    _: str = Depends(require_admin_token),
) -> JSONResponse:
    """JSON snapshot of token state. Defaults to SHOPEE_SHOP_ID from env."""
    sid = shop_id or settings.SHOPEE_SHOP_ID
    return JSONResponse(await auth_status(sid))


@router.get("/auth-url")
async def auth_url_endpoint(
    redirect: str = Query(..., description="OAuth redirect URI"),
    _: str = Depends(require_admin_token),
) -> JSONResponse:
    """Return signed OAuth authorization URL for manual approval flow.
    Mints a one-shot `state` token so the resulting URL survives the
    callback's anti-CSRF check."""
    try:
        state = await issue_oauth_state()
        result = build_auth_url(redirect, state=state)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return JSONResponse({
        "url": result.url,
        "timestamp": result.timestamp,
        "sandbox": result.sandbox,
        "state": state,
    })


@router.get("/callback", response_class=HTMLResponse)
async def oauth_callback(
    request: Request,
    code: str | None = Query(default=None),
    shop_id: str | None = Query(default=None),
    state: str | None = Query(default=None),
) -> HTMLResponse:
    """
    Shopee redirects here after user approves. Authentication is via the
    OAuth `state` nonce (minted at setup-wizard time, stored in Redis with
    TTL). We can't rely on cookie/query token surviving Shopee's cross-site
    top-level redirect on all browsers, so state is the only reliable
    auth carrier here.
    """
    if not await consume_oauth_state(state):
        raise HTTPException(status_code=401, detail="Invalid or expired OAuth state")

    if not code or not shop_id:
        raise HTTPException(
            status_code=400,
            detail="Missing code or shop_id in Shopee callback",
        )

    try:
        tokens = await exchange_code_for_tokens(code, shop_id)
        await persist_tokens(shop_id, tokens)
    except (ShopeeAuthError, ShopeeError) as e:
        logger.exception("shopee_callback_failed", error=str(e), shop_id=shop_id)
        return HTMLResponse(
            f"<h1>Shopee OAuth failed</h1><pre>{e}</pre>",
            status_code=502,
        )

    status = await auth_status(shop_id)
    return HTMLResponse(
        "<!DOCTYPE html><html><body style='font-family:system-ui;padding:32px'>"
        f"<h1>Shopee OAuth complete ✓</h1>"
        f"<p>shop_id: <code>{shop_id}</code></p>"
        f"<p>Access TTL: <code>{status['access_ttl_seconds']}s</code></p>"
        f"<p>Refresh TTL: <code>{status['refresh_ttl_seconds']}s "
        f"({status['refresh_expires_in_days']} days)</code></p>"
        f"<p>Sandbox: <code>{status['sandbox']}</code></p>"
        "</body></html>",
    )
