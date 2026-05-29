"""Helper function to set CSRF token cookie on GET responses."""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.core.config import settings
from src.core.csrf import generate_csrf_token

if TYPE_CHECKING:
    from fastapi.responses import Response


def set_csrf_cookie(resp: Response) -> str:
    """Generate and set CSRF token cookie on response.

    Returns the token so it can be passed to template context.

    Cookie attributes:
    - httponly=False: JS can read for AJAX requests
    - samesite=strict: prevent CSRF attacks
    - secure: HTTPS only (production/staging)
    - max_age: 1 hour
    """
    csrf_token = generate_csrf_token()
    resp.set_cookie(
        "csrf_token",
        csrf_token,
        max_age=3600,
        httponly=False,
        samesite="strict",
        secure=settings.ENVIRONMENT != "development",
    )
    return csrf_token
