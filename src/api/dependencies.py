"""Admin auth + DB session deps."""

from __future__ import annotations

import hmac
from typing import TYPE_CHECKING

from fastapi import Cookie, Form, Header, HTTPException, Query, status

from src.core.config import settings
from src.core.csrf import verify_csrf_token
from src.core.database import get_async_db as _get_async_db

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fastapi.responses import Response
    from sqlalchemy.ext.asyncio import AsyncSession


async def get_async_db() -> AsyncIterator[AsyncSession]:
    async for session in _get_async_db():
        yield session


def require_admin_token(
    admin_token: str | None = Cookie(default=None),
    token: str | None = Query(default=None),
) -> str:
    # Prefer the explicit query token: it lets an admin recover from a
    # stale/wrong cookie without manually clearing browser state.
    candidate = token or admin_token
    if not hmac.compare_digest(candidate or "", settings.ADMIN_SECRET_TOKEN):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
        )
    return "admin"


def set_admin_cookie(resp: Response) -> None:
    """Persist admin auth across navigation. Called by any admin GET that
    accepts `?token=` so subsequent links (which drop the query string)
    stay authenticated via cookie.

    `secure` is driven by ENVIRONMENT, not Shopee sandbox: a staging or
    production deployment that happens to point at Shopee's sandbox API
    is still served over HTTPS and must keep the Secure flag set."""
    resp.set_cookie(
        "admin_token",
        settings.ADMIN_SECRET_TOKEN,
        max_age=3600,
        httponly=True,
        samesite="lax",
        secure=settings.ENVIRONMENT != "development",
    )


def verify_csrf(
    csrf_token_cookie: str | None = Cookie(default=None, alias="csrf_token"),
    csrf_token_form: str | None = Form(default=None, alias="csrf_token"),
    x_csrf_token: str | None = Header(default=None),
) -> None:
    """Verify CSRF token using double submit cookie pattern.

    Accepts token from:
    1. Form field: csrf_token (POST form data)
    2. Header: X-CSRF-Token (AJAX requests)

    Compares against csrf_token cookie using constant-time comparison.

    Raises:
        HTTPException 403: If token missing or mismatch
    """
    form_token = csrf_token_form or x_csrf_token
    if not verify_csrf_token(csrf_token_cookie, form_token):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF token missing or invalid",
        )
