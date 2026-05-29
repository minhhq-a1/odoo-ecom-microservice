"""Unit tests for stock_worker - simplified to avoid async helper complexity."""

from __future__ import annotations

from unittest.mock import patch

from src.workers.stock_worker import stock_safety_net


def test_stock_safety_net_disabled() -> None:
    """Safety net should return disabled when ENABLE_STOCK_SYNC is False."""
    with patch("src.workers.stock_worker.settings") as mock_settings:
        mock_settings.ENABLE_STOCK_SYNC = False

        result = stock_safety_net()

        assert result["status"] == "disabled"
