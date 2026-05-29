"""Raw webhook event log — audit + forensic trail."""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base


class WebhookEventLog(Base):
    __tablename__ = "webhook_event_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    platform: Mapped[str] = mapped_column(String(20), nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )
    source_ip: Mapped[str | None] = mapped_column(INET)
    signature: Mapped[str | None] = mapped_column(String(255))
    signature_valid: Mapped[bool] = mapped_column(Boolean, nullable=False)
    headers: Mapped[dict | None] = mapped_column(JSONB)
    body_raw: Mapped[bytes | None] = mapped_column(LargeBinary)
    body_size: Mapped[int | None] = mapped_column(Integer)
    event_code: Mapped[int | None] = mapped_column(Integer)
    platform_order_id: Mapped[str | None] = mapped_column(String(100), index=True)
    outbox_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("webhook_outbox.id"))
    duplicate_of: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("webhook_event_log.id"),
    )
    parse_error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (Index("idx_webhook_event_platform_order", "platform", "platform_order_id"),)
