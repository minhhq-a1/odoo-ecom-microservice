"""Round 21 P2-21A: ProductNotFoundError flips OrderMapping + fires alert."""
from __future__ import annotations

import contextlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from src.core.exceptions import ProductNotFoundError


class _FakeDB:
    """In-memory DB stand-in. Records the writes the handler performs."""

    def __init__(self, outbox_row, mapping_row) -> None:
        self.outbox_row = outbox_row
        self.mapping_row = mapping_row
        self.committed = 0

    async def get(self, model, pk):
        if model.__name__ == "WebhookOutbox":
            return self.outbox_row
        return None

    async def execute(self, _stmt):
        # Single query in the handler returns the OrderMapping row.
        result = MagicMock()
        result.scalar_one_or_none = MagicMock(return_value=self.mapping_row)
        return result

    async def commit(self):
        self.committed += 1


def test_product_not_found_flips_outbox_and_mapping_and_alerts(monkeypatch):
    outbox_row = SimpleNamespace(id=42, status="processing", last_error=None)
    mapping_row = SimpleNamespace(
        id=7, platform="shopee", platform_order_id="ORD-1",
        status="failed", last_error=None,
    )
    fake_db = _FakeDB(outbox_row, mapping_row)

    @contextlib.asynccontextmanager
    async def _ctx():
        yield fake_db

    alerts_called: list[list[int]] = []

    async def _fake_alerts(ids):
        alerts_called.append(list(ids))

    monkeypatch.setattr(
        "src.core.database.get_async_db_context", _ctx,
    )
    monkeypatch.setattr(
        "src.services.outbox_service.OutboxService._fire_dead_letter_alerts",
        _fake_alerts,
    )

    # Force OrderService.sync to raise ProductNotFoundError for a missing SKU.
    sync_mock = AsyncMock(side_effect=ProductNotFoundError("MISSING-SKU"))
    monkeypatch.setattr("src.services.order_service.OrderService.sync", sync_mock)

    # Make ShopeeConnector.get_order_detail return a sentinel order (any obj,
    # not used because sync is mocked). Patch the async context manager.
    fake_conn = MagicMock()
    fake_conn.get_order_detail = AsyncMock(return_value=SimpleNamespace())
    conn_cls = MagicMock()
    conn_cls.return_value.__aenter__ = AsyncMock(return_value=fake_conn)
    conn_cls.return_value.__aexit__ = AsyncMock(return_value=None)
    monkeypatch.setattr("src.connectors.shopee.ShopeeConnector", conn_cls)

    # Disable dry-run so the order branch runs to completion.
    monkeypatch.setattr(
        "src.core.config.settings.MIDDLEWARE_DRY_RUN", False, raising=False,
    )

    from src.workers.order_worker import process_webhook_event

    result = process_webhook_event.apply(
        kwargs=dict(
            outbox_id=42, platform="shopee", event_type="order",
            platform_order_id="ORD-1", payload={"x": 1},
        ),
    ).get()

    assert result == {"status": "dead_letter", "reason": "product_not_found", "sku": "MISSING-SKU"}
    assert outbox_row.status == "dead_letter"
    assert "MISSING-SKU" in outbox_row.last_error
    assert mapping_row.status == "dead_letter"
    assert "MISSING-SKU" in mapping_row.last_error
    assert alerts_called == [[42]]
    assert fake_db.committed >= 1


def test_product_not_found_alert_failure_preserves_dead_letter_return(monkeypatch):
    outbox_row = SimpleNamespace(id=43, status="processing", last_error=None)
    mapping_row = SimpleNamespace(
        id=8, platform="shopee", platform_order_id="ORD-2",
        status="failed", last_error=None,
    )
    fake_db = _FakeDB(outbox_row, mapping_row)

    @contextlib.asynccontextmanager
    async def _ctx():
        yield fake_db

    async def _fake_alerts(_ids):
        raise RuntimeError("slack down")

    monkeypatch.setattr("src.core.database.get_async_db_context", _ctx)
    monkeypatch.setattr(
        "src.services.outbox_service.OutboxService._fire_dead_letter_alerts",
        _fake_alerts,
    )
    monkeypatch.setattr(
        "src.services.order_service.OrderService.sync",
        AsyncMock(side_effect=ProductNotFoundError("MISSING-SKU")),
    )

    fake_conn = MagicMock()
    fake_conn.get_order_detail = AsyncMock(return_value=SimpleNamespace())
    conn_cls = MagicMock()
    conn_cls.return_value.__aenter__ = AsyncMock(return_value=fake_conn)
    conn_cls.return_value.__aexit__ = AsyncMock(return_value=None)
    monkeypatch.setattr("src.connectors.shopee.ShopeeConnector", conn_cls)
    monkeypatch.setattr(
        "src.core.config.settings.MIDDLEWARE_DRY_RUN", False, raising=False,
    )

    from src.workers.order_worker import process_webhook_event

    result = process_webhook_event.apply(
        kwargs=dict(
            outbox_id=43, platform="shopee", event_type="order",
            platform_order_id="ORD-2", payload={"x": 1},
        ),
    ).get()

    assert result == {"status": "dead_letter", "reason": "product_not_found", "sku": "MISSING-SKU"}
    assert outbox_row.status == "dead_letter"
    assert mapping_row.status == "dead_letter"
    assert fake_db.committed == 1
