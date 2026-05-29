"""Admin auth + DB session deps."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import Cookie, HTTPException, Query, status

from src.core.config import settings
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
    if candidate != settings.ADMIN_SECRET_TOKEN:
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
