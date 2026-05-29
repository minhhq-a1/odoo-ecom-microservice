"""Nightly reconciliation result log."""

from datetime import date, datetime

from sqlalchemy import Date, DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base


class ReconciliationLog(Base):
    __tablename__ = "reconciliation_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    platform: Mapped[str] = mapped_column(String(20), nullable=False)
    run_date: Mapped[date] = mapped_column(Date, nullable=False)
    orders_checked: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    orders_matched: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    orders_missing: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    orders_extra: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    auto_fixed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    needs_review: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    details: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
