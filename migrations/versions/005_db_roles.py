"""DB roles & row-level grants (run after schema is in place).

Revision ID: 005_db_roles
Revises: 004_audit_log

Role passwords are supplied via environment variables at migration time:
  MW_API_PASSWORD       — required for the mw_api login role
  MW_WORKER_PASSWORD    — required for the mw_worker login role
  MW_READONLY_PASSWORD  — required for the mw_readonly login role

In ENVIRONMENT=production these are mandatory and the upgrade aborts if any
are missing.

In non-production environments missing values mean: keep an existing role
as-is (don't rotate the password silently — Codex round 2 P2-A), or generate
a one-shot password if the role doesn't exist yet (logged to migration
output for the operator to record).
"""
import os
import secrets

from alembic import op
from sqlalchemy import text

revision = "005_db_roles"
down_revision = "004_audit_log"
branch_labels = None
depends_on = None


_ROLE_ENV_VARS = {
    "mw_api": "MW_API_PASSWORD",
    "mw_worker": "MW_WORKER_PASSWORD",
    "mw_readonly": "MW_READONLY_PASSWORD",
}


def _resolve_role_passwords(existing_roles: set[str]) -> dict[str, str | None]:
    """Return per-role password or None (None ⇒ do not ALTER ROLE).

    Production: env var must be set for every role; missing ⇒ abort.
    Non-prod: env var preferred; missing ⇒ keep existing role untouched
    (None), or generate a one-shot password if role doesn't exist yet.
    """
    environment = (os.environ.get("ENVIRONMENT") or "development").lower()
    is_production = environment == "production"

    resolved: dict[str, str | None] = {}
    missing: list[str] = []
    for role, env_var in _ROLE_ENV_VARS.items():
        value = os.environ.get(env_var)
        if value:
            resolved[role] = value
        elif is_production:
            missing.append(env_var)
        elif role in existing_roles:
            # P2-A: do NOT silently rotate an existing dev role's password.
            print(
                f"[migration 005_db_roles] {env_var} not set; "
                f"keeping existing password for role {role} (no rotate)."
            )
            resolved[role] = None
        else:
            resolved[role] = secrets.token_urlsafe(32)
            print(
                f"[migration 005_db_roles] {env_var} not set; "
                f"generated ephemeral password for new role {role}. "
                "Rotate before sharing this database."
            )

    if missing:
        raise RuntimeError(
            "Refusing to create DB roles with placeholder passwords in "
            f"ENVIRONMENT=production. Missing: {', '.join(missing)}. "
            "Set these environment variables before running migrations."
        )
    return resolved


def upgrade() -> None:
    bind = op.get_bind()
    existing = {
        row[0] for row in bind.execute(text(
            "SELECT rolname FROM pg_roles WHERE rolname = ANY(:names)"
        ), {"names": list(_ROLE_ENV_VARS.keys())}).fetchall()
    }
    passwords = _resolve_role_passwords(existing)
    for role, password in passwords.items():
        if password is None:
            continue  # existing role, no env var — leave untouched
        safe_password = password.replace("'", "''")
        op.execute(f"""
        DO $$
        BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='{role}') THEN
            CREATE ROLE {role} WITH LOGIN PASSWORD '{safe_password}';
          ELSE
            ALTER ROLE {role} WITH LOGIN PASSWORD '{safe_password}';
          END IF;
        END$$;
        """)

    op.execute("""
    DO $$
    DECLARE
      db_name text := current_database();
    BEGIN
      EXECUTE format('GRANT CONNECT ON DATABASE %I TO mw_api, mw_worker, mw_readonly', db_name);
    END$$;
    """)
    op.execute("GRANT USAGE ON SCHEMA public TO mw_api, mw_worker, mw_readonly")

    op.execute("""
    GRANT INSERT, SELECT ON webhook_outbox    TO mw_api;
    GRANT INSERT, SELECT ON webhook_event_log TO mw_api;
    GRANT SELECT          ON order_mapping    TO mw_api;
    GRANT SELECT          ON platform_config  TO mw_api;
    GRANT SELECT          ON product_mapping  TO mw_api;
    GRANT INSERT          ON audit_log        TO mw_api;
    GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO mw_api;
    """)

    op.execute("""
    GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO mw_worker;
    GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO mw_worker;
    """)

    op.execute("GRANT SELECT ON ALL TABLES IN SCHEMA public TO mw_readonly")


def downgrade() -> None:
    op.execute("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM mw_api, mw_worker, mw_readonly")
    op.execute("REVOKE ALL ON SCHEMA public FROM mw_api, mw_worker, mw_readonly")
