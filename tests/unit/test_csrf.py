"""Unit tests for CSRF protection."""

from __future__ import annotations

from src.core.csrf import generate_csrf_token, verify_csrf_token


def test_generate_csrf_token_length() -> None:
    """Token should be 64 hex chars (32 bytes)."""
    token = generate_csrf_token()
    assert len(token) == 64
    assert all(c in "0123456789abcdef" for c in token)


def test_generate_csrf_token_randomness() -> None:
    """Each token should be unique."""
    tokens = {generate_csrf_token() for _ in range(100)}
    assert len(tokens) == 100


def test_verify_csrf_token_success() -> None:
    """Matching tokens should verify."""
    token = generate_csrf_token()
    assert verify_csrf_token(token, token) is True


def test_verify_csrf_token_mismatch() -> None:
    """Mismatched tokens should fail."""
    token1 = generate_csrf_token()
    token2 = generate_csrf_token()
    assert verify_csrf_token(token1, token2) is False


def test_verify_csrf_token_empty() -> None:
    """Empty or None tokens should fail."""
    token = generate_csrf_token()
    assert verify_csrf_token(None, token) is False
    assert verify_csrf_token(token, None) is False
    assert verify_csrf_token("", token) is False
    assert verify_csrf_token(token, "") is False
    assert verify_csrf_token(None, None) is False
