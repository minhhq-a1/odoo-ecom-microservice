"""HMAC-SHA256 signing for Shopee Open Platform v2."""

from __future__ import annotations

import hashlib
import hmac
import time


def sign_request(
    partner_id: str,
    api_path: str,
    partner_key: str,
    access_token: str = "",
    shop_id: str = "",
    timestamp: int | None = None,
) -> tuple[str, int]:
    """
    Sign authenticated Shopee v2 request.
    Base string: partner_id + api_path + timestamp + access_token + shop_id.
    Returns (signature_hex, timestamp).
    """
    ts = timestamp if timestamp is not None else int(time.time())
    base = f"{partner_id}{api_path}{ts}{access_token}{shop_id}"
    digest = hmac.new(partner_key.encode("utf-8"), base.encode("utf-8"), hashlib.sha256).hexdigest()
    return digest, ts


def sign_public(
    partner_id: str,
    api_path: str,
    partner_key: str,
    timestamp: int | None = None,
) -> tuple[str, int]:
    """
    Sign public Shopee endpoints (no shop context yet): auth_partner, token/get.
    Base string: partner_id + api_path + timestamp.
    """
    ts = timestamp if timestamp is not None else int(time.time())
    base = f"{partner_id}{api_path}{ts}"
    digest = hmac.new(partner_key.encode("utf-8"), base.encode("utf-8"), hashlib.sha256).hexdigest()
    return digest, ts


def verify_webhook(url: str, body: bytes, signature: str, partner_key: str) -> bool:
    """Verify a Shopee v2 push (webhook) signature.

    Shopee signs each push as HMAC-SHA256(partner_key, push_url + raw_body)
    (hex) and delivers it in the `Authorization` header. The base string is
    the EXACT registered callback URL concatenated with the raw request body
    bytes — re-serialized JSON will not match. `url` must be the registered
    push URL (settings.SHOPEE_WEBHOOK_URL), not a proxy-rewritten request URL.
    """
    if not signature or not url:
        return False
    base = url.encode("utf-8") + body
    expected = hmac.new(partner_key.encode("utf-8"), base, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)
