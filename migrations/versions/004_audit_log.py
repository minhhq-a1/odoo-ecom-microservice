"""audit log.

Revision ID: 004_audit_log
Revises: 003_reconciliation
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import INET, JSONB

revision = "004_audit_log"
down_revision = "003_reconciliation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("actor", sa.String(100), nullable=False),
        sa.Column("action", sa.String(50), nullable=False),
        sa.Column("target", sa.String(200)),
        sa.Column("ip", INET),
        sa.Column("user_agent", sa.Text),
        sa.Column("payload", JSONB),
        sa.Column("success", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("error", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    op.create_index("idx_audit_actor_time", "audit_log", ["actor", "created_at"])
    op.create_index("idx_audit_action_time", "audit_log", ["action", "created_at"])


def downgrade() -> None:
    op.drop_index("idx_audit_action_time", table_name="audit_log")
    op.drop_index("idx_audit_actor_time", table_name="audit_log")
    op.drop_table("audit_log")
