"""Base connector abstract class."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from datetime import date

    from src.schemas.unified import StockUpdateRequest, UnifiedOrder


class BaseConnector(ABC):
    platform: str

    @abstractmethod
    async def get_order_detail(self, platform_order_id: str) -> UnifiedOrder: ...

    @abstractmethod
    async def get_orders_by_date(self, day: date) -> AsyncIterator[UnifiedOrder]: ...

    @abstractmethod
    async def update_stock(self, request: StockUpdateRequest) -> None: ...

    @abstractmethod
    async def confirm_shipment(self, platform_order_id: str, tracking_no: str) -> None: ...
