"""Unit tests for csrf_helper module."""

from __future__ import annotations

from unittest.mock import MagicMock

from src.api.csrf_helper import set_csrf_cookie


def test_set_csrf_cookie() -> None:
    """set_csrf_cookie should set a cookie on the response."""
    mock_response = MagicMock()
    mock_response.set_cookie = MagicMock()

    token = set_csrf_cookie(mock_response)

    assert token is not None
    assert len(token) == 64  # 32 bytes hex = 64 chars
    mock_response.set_cookie.assert_called_once()

    # Check cookie parameters
    call_args = mock_response.set_cookie.call_args
    assert call_args[0][0] == "csrf_token"  # Cookie name
    assert call_args[0][1] == token  # Cookie value


def test_set_csrf_cookie_generates_unique_tokens() -> None:
    """set_csrf_cookie should generate unique tokens each time."""
    mock_response1 = MagicMock()
    mock_response2 = MagicMock()

    token1 = set_csrf_cookie(mock_response1)
    token2 = set_csrf_cookie(mock_response2)

    assert token1 != token2
