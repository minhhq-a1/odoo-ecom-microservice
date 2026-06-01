"""src.connectors.shopee.signing unit tests."""

from __future__ import annotations

from src.connectors.shopee.signing import sign_request, verify_webhook


def test_sign_request_deterministic() -> None:
    sig1, _ts = sign_request(
        "partner1", "/api/v2/x", "key1", access_token="atk", shop_id="ship1", timestamp=1700000000
    )
    sig2, _ = sign_request(
        "partner1", "/api/v2/x", "key1", access_token="atk", shop_id="ship1", timestamp=1700000000
    )
    assert sig1 == sig2
    assert len(sig1) == 64


def test_verify_webhook_valid() -> None:
    # Shopee v2 push signs HMAC(partner_key, push_url + raw_body).
    url = "https://mw.example.com/webhook/shopee"
    body = b'{"code":3}'
    import hashlib
    import hmac

    expected = hmac.new(b"secret_key", url.encode() + body, hashlib.sha256).hexdigest()
    assert verify_webhook(url, body, expected, "secret_key") is True
    # body-only signature (old, wrong scheme) must be rejected
    body_only = hmac.new(b"secret_key", body, hashlib.sha256).hexdigest()
    assert verify_webhook(url, body, body_only, "secret_key") is False
    # tampered URL must be rejected
    assert (
        verify_webhook("https://evil.example.com/webhook/shopee", body, expected, "secret_key")
        is False
    )
    assert verify_webhook(url, body, "wrong", "secret_key") is False
    assert verify_webhook(url, body, "", "secret_key") is False
    assert verify_webhook("", body, expected, "secret_key") is False
