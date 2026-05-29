"""Order mapping: platform order ↔ Odoo SO."""

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base, TimestampMixin


class OrderMapping(Base, TimestampMixin):
    __tablename__ = "order_mapping"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    platform: Mapped[str] = mapped_column(String(20), nullable=False)
    platform_order_id: Mapped[str] = mapped_column(String(100), nullable=False)
    platform_order_sn: Mapped[str | None] = mapped_column(String(100))
    odoo_order_id: Mapped[int | None] = mapped_column(Integer)
    odoo_order_name: Mapped[str | None] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("platform", "platform_order_id", name="uq_order_platform_id"),
        Index("idx_order_platform_status_created", "platform", "status", "created_at"),
        Index(
            "idx_order_status_updated",
            "status",
            "updated_at",
            postgresql_where="status IN ('failed', 'dead_letter')",
        ),
    )
