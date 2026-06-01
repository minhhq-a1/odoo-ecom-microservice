"""Partial index on webhook_event_log.signature for replay DB fallback.

Revision ID: 007_webhook_signature_index
Revises: 006_admin_grants

P1 (review): webhooks._is_replay falls back to a durable lookup against
webhook_event_log when Redis is unavailable, querying by (signature,
signature_valid=true). This partial index keeps that fallback cheap under
load. CONCURRENTLY so it does not lock the table on a live deployment.
"""
from alembic import op

revision = "007_webhook_signature_index"
down_revision = "006_admin_grants"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # CONCURRENTLY cannot run inside a transaction block.
    with op.get_context().autocommit_block():
        op.execute(
            """
            CREATE INDEX CONCURRENTLY IF NOT EXISTS
                idx_webhook_event_signature_valid
            ON webhook_event_log (signature)
            WHERE signature_valid;
            """
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "DROP INDEX CONCURRENTLY IF EXISTS idx_webhook_event_signature_valid;"
        )
