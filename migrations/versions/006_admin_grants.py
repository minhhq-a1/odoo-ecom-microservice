"""Extend mw_api grants for admin UI writes.

Revision ID: 006_admin_grants
Revises: 005_db_roles

Round 5 P1: admin config routes (/admin/config/platforms, /products, /stock)
mutate platform_config, product_mapping, product_bundle_component,
stock_allocation_config, audit_log. Migration 005 only granted SELECT on
some of those to mw_api, breaking admin writes when API runs as mw_api.

This migration grants INSERT/UPDATE/DELETE to mw_api on the admin-managed
tables and SELECT on audit_log so the admin UI can list it.
"""
from alembic import op

revision = "006_admin_grants"
down_revision = "005_db_roles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    GRANT INSERT, UPDATE, DELETE ON platform_config             TO mw_api;
    GRANT INSERT, UPDATE, DELETE ON product_mapping             TO mw_api;
    GRANT UPDATE                 ON order_mapping               TO mw_api;
    GRANT UPDATE                 ON webhook_outbox              TO mw_api;
    GRANT SELECT, INSERT, UPDATE, DELETE
                                 ON product_bundle_components   TO mw_api;
    GRANT SELECT, INSERT, UPDATE, DELETE
                                 ON stock_allocation_config     TO mw_api;
    GRANT SELECT                  ON audit_log                   TO mw_api;
    """)


def downgrade() -> None:
    op.execute("""
    REVOKE INSERT, UPDATE, DELETE ON platform_config             FROM mw_api;
    REVOKE INSERT, UPDATE, DELETE ON product_mapping             FROM mw_api;
    REVOKE UPDATE                 ON order_mapping               FROM mw_api;
    REVOKE UPDATE                 ON webhook_outbox              FROM mw_api;
    REVOKE SELECT, INSERT, UPDATE, DELETE
                                  ON product_bundle_components   FROM mw_api;
    REVOKE SELECT, INSERT, UPDATE, DELETE
                                  ON stock_allocation_config     FROM mw_api;
    REVOKE SELECT                 ON audit_log                    FROM mw_api;
    """)
