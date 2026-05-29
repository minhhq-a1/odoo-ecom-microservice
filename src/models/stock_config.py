"""Stock allocation config per (SKU, platform)."""

from sqlalchemy import Boolean, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base


class StockAllocationConfig(Base):
    __tablename__ = "stock_allocation_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    odoo_sku: Mapped[str] = mapped_column(String(100), nullable=False)
    platform: Mapped[str] = mapped_column(String(20), nullable=False)
    allocation_pct: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False)
    buffer_pct: Mapped[float] = mapped_column(Numeric(5, 2), default=10.00, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    __table_args__ = (UniqueConstraint("odoo_sku", "platform", name="uq_stock_sku_platform"),)
