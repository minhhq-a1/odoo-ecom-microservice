"""Unit tests for dependencies module."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import HTTPException

from src.api.dependencies import require_admin_token


def test_require_admin_token_valid_cookie() -> None:
    """Valid admin token in cookie should pass."""
    with patch("src.api.dependencies.settings") as mock_settings:
        mock_settings.ADMIN_SECRET_TOKEN = "test_token"
        result = require_admin_token(admin_token="test_token", token=None)
        assert result == "admin"


def test_require_admin_token_valid_query() -> None:
    """Valid admin token in query should pass."""
    with patch("src.api.dependencies.settings") as mock_settings:
        mock_settings.ADMIN_SECRET_TOKEN = "test_token"
        result = require_admin_token(admin_token=None, token="test_token")
        assert result == "admin"


def test_require_admin_token_query_takes_precedence() -> None:
    """Query token should take precedence over cookie."""
    with patch("src.api.dependencies.settings") as mock_settings:
        mock_settings.ADMIN_SECRET_TOKEN = "correct_token"
        result = require_admin_token(admin_token="wrong_cookie", token="correct_token")
        assert result == "admin"


def test_require_admin_token_invalid() -> None:
    """Invalid admin token should raise 401."""
    with patch("src.api.dependencies.settings") as mock_settings:
        mock_settings.ADMIN_SECRET_TOKEN = "correct_token"
        with pytest.raises(HTTPException) as exc_info:
            require_admin_token(admin_token="wrong_token", token=None)
        assert exc_info.value.status_code == 401


def test_require_admin_token_missing() -> None:
    """Missing admin token should raise 401."""
    with patch("src.api.dependencies.settings") as mock_settings:
        mock_settings.ADMIN_SECRET_TOKEN = "test_token"
        with pytest.raises(HTTPException) as exc_info:
            require_admin_token(admin_token=None, token=None)
        assert exc_info.value.status_code == 401
