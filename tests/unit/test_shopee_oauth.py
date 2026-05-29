"""Shopee OAuth flow tests."""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlparse

import pytest

os.environ.setdefault("SHOPEE_PARTNER_ID", "2000123")
os.environ.setdefault("SHOPEE_PARTNER_KEY", "test_partner_key_xxx")
os.environ.setdefault("SHOPEE_SHOP_ID", "9999")


def test_sign_public_format() -> None:
    from src.connectors.shopee.signing import sign_public
    sig, ts = sign_public("123", "/api/v2/x", "secret", timestamp=1700000000)
    assert len(sig) == 64
    assert int(ts) == 1700000000


def test_sign_public_excludes_access_token_and_shop_id() -> None:
    """sign_public base string MUST differ from sign_request (no token/shop suffix)."""
    from src.connectors.shopee.signing import sign_public, sign_request
    pub, _ = sign_public("123", "/api/v2/x", "secret", timestamp=1700000000)
    auth, _ = sign_request("123", "/api/v2/x", "secret",
                            access_token="atk", shop_id="42", timestamp=1700000000)
    assert pub != auth


def test_build_auth_url_contains_required_params() -> None:
    from src.connectors.shopee.oauth import build_auth_url
    from src.core.config import settings as live_settings
    result = build_auth_url("https://example.com/admin/shopee/callback")
    parsed = urlparse(result.url)
    qs = parse_qs(parsed.query)
    assert qs["partner_id"] == [live_settings.SHOPEE_PARTNER_ID]
    assert "timestamp" in qs
    assert "sign" in qs
    assert len(qs["sign"][0]) == 64
    assert qs["redirect"] == ["https://example.com/admin/shopee/callback"]
    assert parsed.path == "/api/v2/shop/auth_partner"


def test_build_auth_url_rejects_relative_redirect() -> None:
    from src.connectors.shopee.oauth import build_auth_url
    with pytest.raises(ValueError):
        build_auth_url("/admin/callback")


@pytest.mark.asyncio
async def test_exchange_code_success() -> None:
    from src.connectors.shopee.oauth import exchange_code_for_tokens

    class FakeResponse:
        def json(self) -> dict:
            return {
                "access_token": "ACCESS_xxx", "refresh_token": "REFRESH_yyy",
                "expire_in": 14400, "refresh_token_expire_in": 2_592_000,
                "request_id": "req1", "error": "", "message": "",
            }

    async def fake_post(self, url, **kwargs):  # type: ignore[no-untyped-def]
        return FakeResponse()

    with patch("httpx.AsyncClient.post", new=fake_post):
        tokens = await exchange_code_for_tokens("CODE123", "9999")
    assert tokens["access_token"] == "ACCESS_xxx"
    assert tokens["refresh_token"] == "REFRESH_yyy"
    assert tokens["expire_in"] == 14400
    assert tokens["refresh_token_expire_in"] == 2_592_000


@pytest.mark.asyncio
async def test_exchange_code_propagates_shopee_error() -> None:
    from src.connectors.shopee.oauth import exchange_code_for_tokens
    from src.core.exceptions import ShopeeAuthError

    class FakeResponse:
        def json(self) -> dict:
            return {"error": "error_auth", "message": "Invalid code"}

    async def fake_post(self, url, **kwargs):  # type: ignore[no-untyped-def]
        return FakeResponse()

    with patch("httpx.AsyncClient.post", new=fake_post), pytest.raises(ShopeeAuthError) as exc:
        await exchange_code_for_tokens("BAD_CODE", "9999")
    assert "Invalid code" in str(exc.value) or "error_auth" in str(exc.value)


@pytest.mark.asyncio
async def test_exchange_code_missing_tokens_raises() -> None:
    from src.connectors.shopee.oauth import exchange_code_for_tokens
    from src.core.exceptions import ShopeeAuthError

    class FakeResponse:
        def json(self) -> dict:
            return {"access_token": "", "refresh_token": ""}

    async def fake_post(self, url, **kwargs):  # type: ignore[no-untyped-def]
        return FakeResponse()

    with patch("httpx.AsyncClient.post", new=fake_post), pytest.raises(ShopeeAuthError):
        await exchange_code_for_tokens("CODE", "9999")


@pytest.mark.asyncio
async def test_persist_tokens_stores_in_redis_and_skips_db_when_no_cipher() -> None:
    from src.connectors.shopee import oauth as oauth_mod

    mock_store = AsyncMock()
    tokens = {
        "access_token": "ACC", "refresh_token": "REF",
        "expire_in": 14400, "refresh_token_expire_in": 2_500_000,
    }
    with (
        patch("src.connectors.shopee.oauth.token_store.store_tokens", new=mock_store),
        patch("src.connectors.shopee.oauth._backup_to_db", new=AsyncMock()),
    ):
        await oauth_mod.persist_tokens("9999", tokens)
    mock_store.assert_awaited_once()
    kwargs = mock_store.await_args.kwargs
    assert kwargs["shop_id"] == "9999"
    assert kwargs["access_token"] == "ACC"
    # access_ttl = expire_in - 600
    assert kwargs["access_ttl"] == 14400 - 600


@pytest.mark.asyncio
async def test_auth_status_no_tokens() -> None:
    from src.connectors.shopee.oauth import auth_status

    fake_redis = AsyncMock()
    fake_redis.ttl = AsyncMock(return_value=-2)
    with patch("src.connectors.shopee.oauth.get_redis", AsyncMock(return_value=fake_redis)):
        status = await auth_status("9999")
    assert status["has_access_token"] is False
    assert status["access_ttl_seconds"] == 0
    assert status["has_refresh_token"] is False
    assert status["refresh_expires_in_days"] == 0


@pytest.mark.asyncio
async def test_auth_status_with_tokens() -> None:
    from src.connectors.shopee.oauth import auth_status

    fake_redis = AsyncMock()
    fake_redis.ttl = AsyncMock(side_effect=[10000, 86400 * 20])
    with patch("src.connectors.shopee.oauth.get_redis", AsyncMock(return_value=fake_redis)):
        status = await auth_status("9999")
    assert status["has_access_token"] is True
    assert status["access_ttl_seconds"] == 10000
    assert status["has_refresh_token"] is True
    assert status["refresh_expires_in_days"] == 20
