"""Status machine + phone normalize tests."""

from __future__ import annotations

import pytest

from src.core.utils import normalize_vn_phone, safe_int
from src.schemas.unified import OrderStatus, should_update_status


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("0901234567", "0901234567"),
        ("+84901234567", "0901234567"),
        ("84 901-234-567", "0901234567"),
        ("(+84) 901.234.567", "0901234567"),
        ("", ""),
    ],
)
def test_normalize_vn_phone(raw: str, expected: str) -> None:
    assert normalize_vn_phone(raw) == expected


@pytest.mark.parametrize(
    ("current", "new", "expected"),
    [
        (OrderStatus.PENDING, OrderStatus.CONFIRMED, True),
        (OrderStatus.SHIPPED, OrderStatus.CONFIRMED, False),
        (OrderStatus.PENDING, OrderStatus.CANCELLED, True),
        (OrderStatus.SHIPPED, OrderStatus.CANCELLED, False),
        (OrderStatus.DELIVERED, OrderStatus.RETURN_REQUESTED, True),
    ],
)
def test_status_machine(current: OrderStatus, new: OrderStatus, expected: bool) -> None:
    assert should_update_status(current, new) is expected


def test_safe_int() -> None:
    assert safe_int("42") == 42
    assert safe_int("abc", default=99) == 99
    assert safe_int(None, default=0) == 0
