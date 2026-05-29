"""reconciliation + price sync logs.

Revision ID: 003_reconciliation
Revises: 002_outbox
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "003_reconciliation"
down_revision = "002_outbox"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "reconciliation_log",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("platform", sa.String(20), nullable=False),
        sa.Column("run_date", sa.Date, nullable=False),
        sa.Column("orders_checked", sa.Integer, nullable=False, server_default="0"),
        sa.Column("orders_matched", sa.Integer, nullable=False, server_default="0"),
        sa.Column("orders_missing", sa.Integer, nullable=False, server_default="0"),
        sa.Column("orders_extra", sa.Integer, nullable=False, server_default="0"),
        sa.Column("auto_fixed", sa.Integer, nullable=False, server_default="0"),
        sa.Column("needs_review", sa.Integer, nullable=False, server_default="0"),
        sa.Column("details", JSONB),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    op.create_index("idx_reconciliation_platform_date", "reconciliation_log",
                    ["platform", "run_date"])

    op.create_table(
        "price_sync_log",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("platform", sa.String(20), nullable=False),
        sa.Column("odoo_sku", sa.String(100), nullable=False),
        sa.Column("old_price", sa.Numeric(15, 2)),
        sa.Column("new_price", sa.Numeric(15, 2)),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("error_message", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    op.create_index("idx_price_sync_sku_platform", "price_sync_log",
                    ["odoo_sku", "platform"])


def downgrade() -> None:
    op.drop_index("idx_price_sync_sku_platform", table_name="price_sync_log")
    op.drop_table("price_sync_log")
    op.drop_index("idx_reconciliation_platform_date", table_name="reconciliation_log")
    op.drop_table("reconciliation_log")
