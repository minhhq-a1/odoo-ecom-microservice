"""outbox + webhook event log.

Revision ID: 002_outbox
Revises: 001_initial
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import INET, JSONB

revision = "002_outbox"
down_revision = "001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "webhook_outbox",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("platform", sa.String(20), nullable=False),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("event_code", sa.Integer),
        sa.Column("platform_order_id", sa.String(100)),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("signature", sa.String(255)),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("retry_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("max_retries", sa.Integer, nullable=False, server_default="5"),
        sa.Column("last_error", sa.Text),
        sa.Column("process_after", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    op.create_index("idx_outbox_pending", "webhook_outbox",
                    ["status", "process_after"],
                    postgresql_where=sa.text("status IN ('pending', 'failed')"))
    op.create_index("idx_outbox_platform_order", "webhook_outbox",
                    ["platform", "platform_order_id"])

    op.create_table(
        "webhook_event_log",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("platform", sa.String(20), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("source_ip", INET),
        sa.Column("signature", sa.String(255)),
        sa.Column("signature_valid", sa.Boolean, nullable=False),
        sa.Column("headers", JSONB),
        sa.Column("body_raw", sa.LargeBinary),
        sa.Column("body_size", sa.Integer),
        sa.Column("event_code", sa.Integer),
        sa.Column("platform_order_id", sa.String(100)),
        sa.Column("outbox_id", sa.BigInteger, sa.ForeignKey("webhook_outbox.id")),
        sa.Column("duplicate_of", sa.BigInteger, sa.ForeignKey("webhook_event_log.id")),
        sa.Column("parse_error", sa.Text),
    )
    op.create_index("idx_webhook_event_received", "webhook_event_log", ["received_at"])
    op.create_index("idx_webhook_event_platform_order", "webhook_event_log",
                    ["platform", "platform_order_id"])
    op.create_index("idx_webhook_event_porder_id", "webhook_event_log", ["platform_order_id"])


def downgrade() -> None:
    op.drop_index("idx_webhook_event_porder_id", table_name="webhook_event_log")
    op.drop_index("idx_webhook_event_platform_order", table_name="webhook_event_log")
    op.drop_index("idx_webhook_event_received", table_name="webhook_event_log")
    op.drop_table("webhook_event_log")
    op.drop_index("idx_outbox_platform_order", table_name="webhook_outbox")
    op.drop_index("idx_outbox_pending", table_name="webhook_outbox")
    op.drop_table("webhook_outbox")
