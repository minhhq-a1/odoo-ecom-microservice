"""Unit tests for alert_service."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.alert_service import AlertService


@pytest.mark.asyncio
async def test_send_slack_alert_success() -> None:
    """Send Slack alert should post to webhook URL."""
    with (
        patch("src.services.alert_service.settings") as mock_settings,
        patch("src.services.alert_service.httpx.AsyncClient") as mock_client,
    ):
        mock_settings.SLACK_WEBHOOK_URL = "https://hooks.slack.com/test"
        mock_client_instance = AsyncMock()
        mock_client_instance.post = AsyncMock()
        mock_client.return_value.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client.return_value.__aexit__ = AsyncMock()

        await AlertService._post_slack("Test message")

        mock_client_instance.post.assert_called_once_with(
            "https://hooks.slack.com/test",
            json={"text": "Test message"},
        )


@pytest.mark.asyncio
async def test_send_slack_alert_no_webhook_configured() -> None:
    """Send Slack alert with no webhook should log warning."""
    with patch("src.services.alert_service.settings") as mock_settings:
        mock_settings.SLACK_WEBHOOK_URL = None

        await AlertService._post_slack("Test message")

        # Should not raise, just log warning


@pytest.mark.asyncio
async def test_send_slack_alert_http_error() -> None:
    """Send Slack alert HTTP error should be caught and logged."""
    with (
        patch("src.services.alert_service.settings") as mock_settings,
        patch("src.services.alert_service.httpx.AsyncClient") as mock_client,
    ):
        mock_settings.SLACK_WEBHOOK_URL = "https://hooks.slack.com/test"
        mock_client_instance = AsyncMock()
        mock_client_instance.post = AsyncMock(side_effect=Exception("Network error"))
        mock_client.return_value.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client.return_value.__aexit__ = AsyncMock()

        await AlertService._post_slack("Test message")

        # Should not raise, just log exception


@pytest.mark.asyncio
async def test_send_dead_letter_alert() -> None:
    """Send dead letter alert should format message correctly."""
    mock_entry = MagicMock()
    mock_entry.id = 123
    mock_entry.platform = "shopee"
    mock_entry.event_type = "order_update"
    mock_entry.platform_order_id = "ORDER-001"
    mock_entry.last_error = "Connection timeout"

    with patch.object(AlertService, "_post_slack", new_callable=AsyncMock) as mock_post:
        await AlertService.send_dead_letter_alert(mock_entry)

        mock_post.assert_called_once()
        call_args = mock_post.call_args[0][0]
        assert ":skull:" in call_args
        assert "123" in call_args
        assert "shopee" in call_args
        assert "order_update" in call_args
        assert "ORDER-001" in call_args
        assert "Connection timeout" in call_args


@pytest.mark.asyncio
async def test_send_missing_sku() -> None:
    """Send missing SKU alert should format message correctly."""
    with patch.object(AlertService, "_post_slack", new_callable=AsyncMock) as mock_post:
        await AlertService.send_missing_sku("SKU-999")

        mock_post.assert_called_once()
        call_args = mock_post.call_args[0][0]
        assert ":warning:" in call_args
        assert "SKU-999" in call_args
        assert "not found" in call_args


@pytest.mark.asyncio
async def test_send_reconciliation_alert() -> None:
    """Send reconciliation alert should format message correctly."""
    needs_review = [
        {"order_sn": "ORDER-001", "reason": "missing"},
        {"order_sn": "ORDER-002", "reason": "status_mismatch"},
    ]

    with patch.object(AlertService, "_post_slack", new_callable=AsyncMock) as mock_post:
        await AlertService.send_reconciliation_alert("shopee", needs_review)

        mock_post.assert_called_once()
        call_args = mock_post.call_args[0][0]
        assert ":mag:" in call_args
        assert "Reconciliation" in call_args
        assert "shopee" in call_args
        assert "2" in call_args
        assert "review" in call_args


@pytest.mark.asyncio
async def test_send_token_expiring() -> None:
    """Send token expiring alert should format message correctly."""
    with patch.object(AlertService, "_post_slack", new_callable=AsyncMock) as mock_post:
        await AlertService.send_token_expiring("123456", 7)

        mock_post.assert_called_once()
        call_args = mock_post.call_args[0][0]
        assert ":key:" in call_args
        assert "123456" in call_args
        assert "7" in call_args
        assert "ngày" in call_args
