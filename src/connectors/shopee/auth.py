"""Shopee token storage + refresh."""
from __future__ import annotations

import asyncio
import time

import httpx

from src.core.config import settings
from src.core.exceptions import ShopeeAuthError
from src.core.logging import get_logger
from src.core.redis import get_redis

logger = get_logger(__name__)

ACCESS_TTL = 14_000      # 3.9h (refresh trước expire 4h)
REFRESH_TTL = 2_500_000  # ~28.9 ngày


def _key(kind: str, shop_id: str) -> str:
    return f"shopee:token:{shop_id}:{kind}"


async def get_access_token(shop_id: str) -> str:
    r = await get_redis()
    token = await r.get(_key("access", shop_id))
    if token:
        return token
    return await refresh(shop_id)


async def get_refresh_token(shop_id: str) -> str:
    r = await get_redis()
    token = await r.get(_key("refresh", shop_id))
    if not token:
        raise ShopeeAuthError(
            "shopee",
            "Refresh token missing from Redis — manual re-auth required",
            shop_id=shop_id,
        )
    return token


async def store_tokens(
    shop_id: str, access_token: str, refresh_token: str,
    access_ttl: int = ACCESS_TTL, refresh_ttl: int = REFRESH_TTL,
) -> None:
    r = await get_redis()
    await r.setex(_key("access", shop_id), access_ttl, access_token)
    await r.setex(_key("refresh", shop_id), refresh_ttl, refresh_token)
    await r.setex(f"shopee:token:{shop_id}:access_issued_at", access_ttl, int(time.time()))


async def refresh(shop_id: str) -> str:
    """Refresh access_token using refresh_token. Idempotent via Redis lock."""
    r = await get_redis()
    lock_key = f"lock:shopee_refresh:{shop_id}"
    got_lock = await r.set(lock_key, "1", ex=60, nx=True)
    if not got_lock:
        # Another worker is refreshing; wait briefly + read
        for _ in range(20):
            await asyncio.sleep(0.5)
            token = await r.get(_key("access", shop_id))
            if token:
                return token
        raise ShopeeAuthError("shopee", "Timed out waiting for token refresh")

    try:
        refresh_token = await get_refresh_token(shop_id)
        from src.connectors.shopee.signing import sign_public

        path = "/api/v2/auth/access_token/get"
        # Shopee public auth endpoint: base string is partner_id+path+ts
        # only — access_token/shop_id are empty here, and using
        # sign_request would append the empty fields literally, which
        # Shopee still accepts in practice but is not the documented
        # signature. Use sign_public to match the spec exactly.
        sign, ts = sign_public(
            settings.SHOPEE_PARTNER_ID, path, settings.SHOPEE_PARTNER_KEY,
        )
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{settings.shopee_base_url[:-7]}{path}",
                params={
                    "partner_id": settings.SHOPEE_PARTNER_ID,
                    "timestamp": ts, "sign": sign,
                },
                json={
                    "refresh_token": refresh_token,
                    "partner_id": int(settings.SHOPEE_PARTNER_ID),
                    "shop_id": int(shop_id),
                },
            )
            data = resp.json()

        if data.get("error"):
            raise ShopeeAuthError("shopee", data.get("message", "refresh failed"), data=data)

        await store_tokens(shop_id, data["access_token"], data["refresh_token"])
        logger.info("shopee_token_refreshed", shop_id=shop_id)
        return data["access_token"]
    finally:
        await r.delete(lock_key)
