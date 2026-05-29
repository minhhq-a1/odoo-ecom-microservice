"""Reconciliation service tests."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.schemas.unified import (
    OrderStatus,
    PaymentMethod,
    Platform,
    UnifiedAddress,
    UnifiedOrder,
)
from src.services.reconciliation_service import (
    ReconciliationService,
)


def _make_order(order_sn: str, status: OrderStatus, total: Decimal) -> UnifiedOrder:
    return UnifiedOrder(
        platform=Platform.SHOPEE,
        platform_order_id=order_sn,
        platform_order_sn=order_sn,
        status=status,
        payment_method=PaymentMethod.COD,
        buyer_platform_id="b1",
        buyer_username="b",
        shipping_address=UnifiedAddress(
            full_name="a",
            phone="0900000000",
            address_line="x",
            district="x",
            province="x",
        ),
        items=[],
        subtotal=total,
        shipping_fee=Decimal("0"),
        platform_discount=Decimal("0"),
        seller_discount=Decimal("0"),
        total_amount=total,
    )


@pytest.mark.asyncio
async def test_auto_fix_only_safe_statuses(monkeypatch) -> None:
    orders = [
        _make_order("M1", OrderStatus.CONFIRMED, Decimal("100000")),
        _make_order("M2", OrderStatus.SHIPPED, Decimal("200000")),  # unsafe → review
    ]

    svc = ReconciliationService.__new__(ReconciliationService)
    svc.odoo = MagicMock()
    svc.order_service = MagicMock()
    svc.order_service.sync = AsyncMock(return_value={"status": "success"})

    monkeypatch.setattr(svc, "_fetch_platform_orders", AsyncMock(return_value=orders))
    monkeypatch.setattr(svc, "_lookup_synced_ids", AsyncMock(return_value=set()))
    monkeypatch.setattr(svc, "_lookup_synced_ids_for_date", AsyncMock(return_value=set()))
    monkeypatch.setattr(svc, "_load_drift_mappings", AsyncMock(return_value=[]))
    monkeypatch.setattr(svc, "_check_field_drift", AsyncMock(return_value=[]))
    monkeypatch.setattr(svc, "_persist", AsyncMock())
    monkeypatch.setattr(
        "src.services.reconciliation_service.AlertService.send_reconciliation_alert",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "src.services.reconciliation_service.settings.MIDDLEWARE_DRY_RUN",
        False,
        raising=False,
    )
    # Bypass REPEATABLE READ snapshot block (no real DB in unit test).
    import contextlib

    @contextlib.asynccontextmanager
    async def _fake_ctx():
        class _FakeDB:
            async def execute(self, *_a, **_k):
                return None

        yield _FakeDB()

    monkeypatch.setattr(
        "src.services.reconciliation_service.get_async_db_context",
        _fake_ctx,
    )

    result = await svc.run_for_platform("shopee", date(2026, 5, 1))
    assert result.orders_checked == 2
    assert result.orders_missing == 2
    assert result.auto_fixed == 1
    assert result.needs_review == 1


@pytest.mark.asyncio
async def test_dry_run_skips_reconciliation(monkeypatch) -> None:
    """Codex round 2 P2-B: dry-run skips the whole reconciliation pass since
    no OrderMapping rows are written by the workers."""
    svc = ReconciliationService.__new__(ReconciliationService)
    svc.odoo = MagicMock()
    svc.order_service = MagicMock()
    svc.order_service.sync = AsyncMock()

    monkeypatch.setattr(svc, "_fetch_platform_orders", AsyncMock())
    monkeypatch.setattr(svc, "_persist", AsyncMock())
    monkeypatch.setattr(
        "src.services.reconciliation_service.settings.MIDDLEWARE_DRY_RUN",
        True,
        raising=False,
    )

    result = await svc.run_for_platform("shopee", date(2026, 5, 1))
    assert result.orders_checked == 0
    assert result.auto_fixed == 0
    assert result.needs_review == 0
    svc._fetch_platform_orders.assert_not_called()
    svc.order_service.sync.assert_not_called()
