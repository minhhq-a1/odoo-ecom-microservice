"""Shopee OAuth flow helpers — auth URL builder + token exchange + persistence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx

from src.connectors.shopee import auth as token_store
from src.connectors.shopee.signing import sign_public
from src.core.config import settings
from src.core.exceptions import ShopeeAuthError, ShopeeError
from src.core.logging import get_logger
from src.core.redis import get_redis

logger = get_logger(__name__)

AUTH_PATH = "/api/v2/shop/auth_partner"
TOKEN_GET_PATH = "/api/v2/auth/token/get"


def _base_origin() -> str:
    """Strip trailing '/api/v2' from configured base URL → origin host root."""
    base = settings.shopee_base_url
    return base[:-7] if base.endswith("/api/v2") else base


@dataclass(frozen=True)
class AuthURL:
    url: str
    timestamp: int
    sandbox: bool


def build_auth_url(redirect_uri: str, state: str | None = None) -> AuthURL:
    """
    Build Shopee OAuth authorization URL.
    User visits URL → approves → Shopee redirects to `redirect_uri` with
    `code` + `shop_id` query params (plus `state` if supplied).

    `state` is the OAuth anti-CSRF/auth-carry token. The caller is expected
    to have stored a corresponding short-TTL Redis entry that the callback
    will verify+consume.
    """
    if not redirect_uri.startswith(("http://", "https://")):
        raise ValueError(f"redirect_uri must include scheme: {redirect_uri!r}")

    sign, ts = sign_public(settings.SHOPEE_PARTNER_ID, AUTH_PATH, settings.SHOPEE_PARTNER_KEY)
    params: dict[str, Any] = {
        "partner_id": settings.SHOPEE_PARTNER_ID,
        "timestamp": ts,
        "sign": sign,
        "redirect": redirect_uri,
    }
    if state:
        params["state"] = state
    qs = urlencode(params)
    url = f"{_base_origin()}{AUTH_PATH}?{qs}"
    return AuthURL(url=url, timestamp=ts, sandbox=settings.SHOPEE_IS_SANDBOX)


OAUTH_STATE_TTL_SEC = 600


async def issue_oauth_state() -> str:
    """Mint a single-use OAuth `state` token bound to TTL in Redis.
    Returned to the admin-authenticated setup wizard; consumed by the
    Shopee callback to prove the redirect originated from us."""
    import secrets

    r = await get_redis()
    state = secrets.token_urlsafe(32)
    await r.setex(f"shopee:oauth:state:{state}", OAUTH_STATE_TTL_SEC, "1")
    return state


async def consume_oauth_state(state: str | None) -> bool:
    """Verify+delete a state token. Returns True iff Redis had it."""
    if not state:
        return False
    r = await get_redis()
    key = f"shopee:oauth:state:{state}"
    deleted = await r.delete(key)
    return bool(deleted)


async def exchange_code_for_tokens(code: str, shop_id: str) -> dict[str, Any]:
    """
    Exchange OAuth `code` (received via redirect) for access + refresh tokens.
    POSTs to /api/v2/auth/token/get. Raises ShopeeAuthError on Shopee error
    response, ShopeeError on transport error.
    """
    sign, ts = sign_public(settings.SHOPEE_PARTNER_ID, TOKEN_GET_PATH, settings.SHOPEE_PARTNER_KEY)
    url = f"{_base_origin()}{TOKEN_GET_PATH}"
    params = {"partner_id": settings.SHOPEE_PARTNER_ID, "timestamp": ts, "sign": sign}
    body = {
        "code": code,
        "shop_id": int(shop_id),
        "partner_id": int(settings.SHOPEE_PARTNER_ID),
    }
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, params=params, json=body)
            data = resp.json()
    except httpx.HTTPError as e:
        raise ShopeeError("shopee", f"Token exchange transport failed: {e}") from e

    err = data.get("error")
    if err:
        raise ShopeeAuthError("shopee", data.get("message", err), code=err, data=data)

    access = data.get("access_token")
    refresh = data.get("refresh_token")
    if not access or not refresh:
        raise ShopeeAuthError(
            "shopee",
            "Token exchange response missing access/refresh",
            data=data,
        )

    expire_in = int(data.get("expire_in", 14400))
    refresh_ttl = int(data.get("refresh_token_expire_in", 2_500_000))
    return {
        "access_token": access,
        "refresh_token": refresh,
        "expire_in": expire_in,
        "refresh_token_expire_in": refresh_ttl,
        "raw": data,
    }


async def persist_tokens(shop_id: str, tokens: dict[str, Any]) -> None:
    """Store tokens.

    Order matters: the encrypted DB backup is attempted FIRST so that a
    cipher/config failure aborts persistence before the Redis copy lands.
    Otherwise a caller seeing 502 from the cipher path would still have live
    tokens cached in Redis with no operator awareness (Codex round 2 P2-D).
    """
    access_ttl = max(60, tokens["expire_in"] - 600)  # refresh 10 min trước expire
    refresh_ttl = max(3600, tokens["refresh_token_expire_in"])

    await _backup_to_db(shop_id, tokens)

    await token_store.store_tokens(
        shop_id=shop_id,
        access_token=tokens["access_token"],
        refresh_token=tokens["refresh_token"],
        access_ttl=access_ttl,
        refresh_ttl=refresh_ttl,
    )
    logger.info(
        "shopee_tokens_stored",
        shop_id=shop_id,
        access_ttl_sec=access_ttl,
        refresh_ttl_sec=refresh_ttl,
    )


async def _backup_to_db(shop_id: str, tokens: dict[str, Any]) -> None:
    """Encrypted backup → platform_config.credentials.
    Production: requires CREDENTIAL_KEYS; raises ConfigError if cipher unavailable.
    Non-production: skip with warning to keep dev/staging frictionless."""
    try:
        from src.core.crypto import get_cipher

        cipher = get_cipher()
    except Exception as e:
        if settings.ENVIRONMENT == "production":
            logger.exception(
                "shopee_token_db_backup_cipher_unavailable_production",
                error=str(e),
            )
            from src.core.exceptions import ConfigError

            raise ConfigError(
                "Encrypted token backup is mandatory in production but "
                f"cipher initialization failed: {e}. Check CREDENTIAL_KEYS.",
            ) from e
        logger.warning("shopee_token_db_backup_skipped_no_cipher", error=str(e))
        return

    from sqlalchemy import select

    from src.core.database import get_async_db_context
    from src.models.platform_config import PlatformConfig

    enc_access = cipher.encrypt(tokens["access_token"])
    enc_refresh = cipher.encrypt(tokens["refresh_token"])
    async with get_async_db_context() as db:
        cfg = (
            await db.execute(
                select(PlatformConfig).where(PlatformConfig.platform == "shopee"),
            )
        ).scalar_one_or_none()
        if cfg is None:
            cfg = PlatformConfig(
                platform="shopee",
                shop_id=shop_id,
                credentials={},
                is_active=True,
            )
            db.add(cfg)
        merged: dict[str, Any] = dict(cfg.credentials or {})
        merged.update(
            {
                "access_token_enc": enc_access,
                "refresh_token_enc": enc_refresh,
                "access_expire_in": tokens["expire_in"],
                "refresh_token_expire_in": tokens["refresh_token_expire_in"],
            }
        )
        cfg.credentials = merged
        cfg.shop_id = shop_id
        await db.commit()
    logger.info("shopee_tokens_db_backup_ok", shop_id=shop_id)


async def auth_status(shop_id: str) -> dict[str, Any]:
    """Return current token state for admin display."""
    r = await get_redis()
    access_ttl = await r.ttl(f"shopee:token:{shop_id}:access")
    refresh_ttl = await r.ttl(f"shopee:token:{shop_id}:refresh")
    has_access = access_ttl is not None and access_ttl > 0
    has_refresh = refresh_ttl is not None and refresh_ttl > 0
    return {
        "shop_id": shop_id,
        "has_access_token": has_access,
        "access_ttl_seconds": access_ttl if has_access else 0,
        "has_refresh_token": has_refresh,
        "refresh_ttl_seconds": refresh_ttl if has_refresh else 0,
        "refresh_expires_in_days": (refresh_ttl // 86400) if has_refresh else 0,
        "sandbox": settings.SHOPEE_IS_SANDBOX,
    }
