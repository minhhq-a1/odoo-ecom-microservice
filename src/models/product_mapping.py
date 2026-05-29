"""Product mapping + bundle components."""
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base


class ProductMapping(Base):
    __tablename__ = "product_mapping"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    platform: Mapped[str] = mapped_column(String(20), nullable=False)
    platform_product_id: Mapped[str] = mapped_column(String(100), nullable=False)
    platform_sku_id: Mapped[str | None] = mapped_column(String(100))
    odoo_product_id: Mapped[int] = mapped_column(Integer, nullable=False)
    odoo_sku: Mapped[str] = mapped_column(String(100), nullable=False)
    mapping_type: Mapped[str] = mapped_column(String(20), default="simple", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("platform", "platform_sku_id", name="uq_product_platform_sku"),
        Index("idx_product_odoo_sku_active", "odoo_sku",
              postgresql_where="is_active = true"),
    )


class ProductBundleComponent(Base):
    __tablename__ = "product_bundle_components"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mapping_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("product_mapping.id", ondelete="CASCADE"), nullable=False,
    )
    odoo_sku: Mapped[str] = mapped_column(String(100), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    __table_args__ = (
        Index("idx_bundle_mapping", "mapping_id"),
        Index("idx_bundle_odoo_sku", "odoo_sku"),
    )
