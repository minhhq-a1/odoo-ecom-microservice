"""src.connectors.shopee.signing unit tests."""
from __future__ import annotations

from src.connectors.shopee.signing import sign_request, verify_webhook


def test_sign_request_deterministic() -> None:
    sig1, _ts = sign_request("partner1", "/api/v2/x", "key1",
                             access_token="atk", shop_id="ship1", timestamp=1700000000)
    sig2, _ = sign_request("partner1", "/api/v2/x", "key1",
                            access_token="atk", shop_id="ship1", timestamp=1700000000)
    assert sig1 == sig2
    assert len(sig1) == 64


def test_verify_webhook_valid() -> None:
    body = b'{"code":3}'
    _sig, _ = sign_request("", "", "secret_key", "", "", timestamp=0)
    import hashlib
    import hmac
    expected = hmac.new(b"secret_key", body, hashlib.sha256).hexdigest()
    assert verify_webhook(body, expected, "secret_key") is True
    assert verify_webhook(body, "wrong", "secret_key") is False
    assert verify_webhook(body, "", "secret_key") is False
