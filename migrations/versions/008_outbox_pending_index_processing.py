"""Extend idx_outbox_pending to cover 'processing' rows.

Revision ID: 008_outbox_pending_index_processing
Revises: 007_webhook_signature_index

P1 (review): the outbox publisher now commits a "processing" claim before
dispatching to the broker, applying a lease via process_after. relay_pending
reclaims expired-lease "processing" rows, so the partial index that backs its
scan must include 'processing' alongside 'pending'/'failed'.
"""
from alembic import op

revision = "008_outbox_pending_index_processing"
down_revision = "007_webhook_signature_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_outbox_pending;")
        op.execute(
            """
            CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_outbox_pending
            ON webhook_outbox (status, process_after)
            WHERE status IN ('pending', 'failed', 'processing');
            """
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_outbox_pending;")
        op.execute(
            """
            CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_outbox_pending
            ON webhook_outbox (status, process_after)
            WHERE status IN ('pending', 'failed');
            """
        )
