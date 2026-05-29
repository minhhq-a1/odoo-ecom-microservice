"""CSRF protection — Double Submit Cookie pattern (stateless)."""

from __future__ import annotations

import hmac
import secrets


def generate_csrf_token() -> str:
    """Generate a random CSRF token (32 bytes hex = 64 chars)."""
    return secrets.token_hex(32)


def verify_csrf_token(cookie_token: str | None, form_token: str | None) -> bool:
    """Verify CSRF token using constant-time comparison.

    Args:
        cookie_token: Token from csrf_token cookie
        form_token: Token from form field or X-CSRF-Token header

    Returns:
        True if tokens match and are non-empty, False otherwise
    """
    if not cookie_token or not form_token:
        return False
    return hmac.compare_digest(cookie_token, form_token)
