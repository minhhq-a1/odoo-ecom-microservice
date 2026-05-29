"""Unit tests for schemas module."""

from __future__ import annotations

from src.schemas.unified import (
    OrderStatus,
    Platform,
)


def test_platform_enum() -> None:
    """Platform enum should have expected values."""
    assert Platform.SHOPEE == "shopee"
    assert Platform.LAZADA == "lazada"
    assert Platform.TIKTOK == "tiktok"


def test_order_status_enum() -> None:
    """OrderStatus enum should have expected values."""
    assert OrderStatus.PENDING == "pending"
    assert OrderStatus.SHIPPED == "shipped"
    assert OrderStatus.DELIVERED == "delivered"
    assert OrderStatus.CANCELLED == "cancelled"


def test_platform_enum_values() -> None:
    """Platform enum should be iterable."""
    platforms = list(Platform)
    assert len(platforms) == 3
    assert Platform.SHOPEE in platforms


def test_order_status_enum_values() -> None:
    """OrderStatus enum should be iterable."""
    statuses = list(OrderStatus)
    assert len(statuses) > 0
    assert OrderStatus.PENDING in statuses
