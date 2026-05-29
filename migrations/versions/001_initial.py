"""initial schema — all core tables.

Revision ID: 001_initial
Revises:
Create Date: 2026-05-23
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import INET, JSONB

revision = "001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.create_table(
        "platform_config",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("platform", sa.String(20), nullable=False, unique=True),
        sa.Column("shop_id", sa.String(100)),
        sa.Column("credentials", JSONB),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("dry_run", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("price_master", sa.String(20), nullable=False, server_default="platform"),
        sa.Column("extra_config", JSONB),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )

    op.create_table(
        "order_mapping",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("platform", sa.String(20), nullable=False),
        sa.Column("platform_order_id", sa.String(100), nullable=False),
        sa.Column("platform_order_sn", sa.String(100)),
        sa.Column("odoo_order_id", sa.Integer),
        sa.Column("odoo_order_name", sa.String(50)),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("retry_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text),
        sa.Column("synced_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.UniqueConstraint("platform", "platform_order_id", name="uq_order_platform_id"),
    )
    op.create_index("idx_order_platform_status_created", "order_mapping",
                    ["platform", "status", "created_at"])
    op.create_index("idx_order_status_updated", "order_mapping",
                    ["status", "updated_at"],
                    postgresql_where=sa.text("status IN ('failed', 'dead_letter')"))
    op.execute(
        "CREATE INDEX idx_order_mapping_pid_trgm ON order_mapping "
        "USING gin (platform_order_id gin_trgm_ops)"
    )

    op.create_table(
        "order_sync_log",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("order_mapping_id", sa.Integer, sa.ForeignKey("order_mapping.id")),
        sa.Column("action", sa.String(50), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("request_payload", JSONB),
        sa.Column("response_payload", JSONB),
        sa.Column("error_message", sa.Text),
        sa.Column("duration_ms", sa.Integer),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    op.create_index("idx_sync_log_mapping_created", "order_sync_log",
                    ["order_mapping_id", sa.text("created_at DESC")])

    op.create_table(
        "product_mapping",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("platform", sa.String(20), nullable=False),
        sa.Column("platform_product_id", sa.String(100), nullable=False),
        sa.Column("platform_sku_id", sa.String(100)),
        sa.Column("odoo_product_id", sa.Integer, nullable=False),
        sa.Column("odoo_sku", sa.String(100), nullable=False),
        sa.Column("mapping_type", sa.String(20), nullable=False, server_default="simple"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.UniqueConstraint("platform", "platform_sku_id", name="uq_product_platform_sku"),
    )
    op.create_index("idx_product_odoo_sku_active", "product_mapping", ["odoo_sku"],
                    postgresql_where=sa.text("is_active = true"))

    op.create_table(
        "product_bundle_components",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("mapping_id", sa.Integer,
                  sa.ForeignKey("product_mapping.id", ondelete="CASCADE"), nullable=False),
        sa.Column("odoo_sku", sa.String(100), nullable=False),
        sa.Column("quantity", sa.Integer, nullable=False, server_default="1"),
    )
    op.create_index("idx_bundle_mapping", "product_bundle_components", ["mapping_id"])
    op.create_index("idx_bundle_odoo_sku", "product_bundle_components", ["odoo_sku"])

    op.create_table(
        "stock_allocation_config",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("odoo_sku", sa.String(100), nullable=False),
        sa.Column("platform", sa.String(20), nullable=False),
        sa.Column("allocation_pct", sa.Numeric(5, 2), nullable=False),
        sa.Column("buffer_pct", sa.Numeric(5, 2), nullable=False, server_default="10.00"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.UniqueConstraint("odoo_sku", "platform", name="uq_stock_sku_platform"),
    )


def downgrade() -> None:
    op.drop_table("stock_allocation_config")
    op.drop_index("idx_bundle_odoo_sku", table_name="product_bundle_components")
    op.drop_index("idx_bundle_mapping", table_name="product_bundle_components")
    op.drop_table("product_bundle_components")
    op.drop_index("idx_product_odoo_sku_active", table_name="product_mapping")
    op.drop_table("product_mapping")
    op.drop_index("idx_sync_log_mapping_created", table_name="order_sync_log")
    op.drop_table("order_sync_log")
    op.execute("DROP INDEX IF EXISTS idx_order_mapping_pid_trgm")
    op.drop_index("idx_order_status_updated", table_name="order_mapping")
    op.drop_index("idx_order_platform_status_created", table_name="order_mapping")
    op.drop_table("order_mapping")
    op.drop_table("platform_config")
