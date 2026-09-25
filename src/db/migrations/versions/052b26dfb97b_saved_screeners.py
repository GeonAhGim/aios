"""saved_screeners — U-1a saved screeners

Revision ID: 052b26dfb97b
Revises: b4bb1b750621
Create Date: 2026-09-17 00:00:00.000000

Spec: docs/specs/L4_product_experience_and_discovery_v1.0.md §2.2,
ADR-2026-09-09-B Decision C(U-1), task-2628(U-1a) decision. Follows the same
tenant top-level table pattern as `chart_indicator_template` (b5bf8da8e058,
CH-17b): `tenant_id` FKs `tenant(id)` (ADR-2026-09-06-G §2 D0), and
`UNIQUE(tenant_id, name)` blocks duplicate names at the schema level so the
application layer has no race window (standard 105 §2.2). The per-tenant
save cap of 50 is a row-count limit that cannot be expressed as a schema
constraint (no existing migration uses this pattern), so the storage
adapter (`adapters/postgres_repository.py`) enforces it at the application
layer with `pg_advisory_xact_lock` + COUNT.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "052b26dfb97b"
down_revision: str | Sequence[str] | None = "b4bb1b750621"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "aios_app"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE saved_screeners (
            id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id    UUID NOT NULL REFERENCES tenant(id),
            name         VARCHAR(120) NOT NULL,
            definition   JSONB NOT NULL,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_saved_screeners_tenant_name UNIQUE (tenant_id, name)
        )
        """
    )
    op.execute("CREATE INDEX idx_saved_screeners_tenant ON saved_screeners(tenant_id)")

    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON saved_screeners TO {_APP_ROLE}")

    # Same tenant_isolation policy as chart_indicator_template (b5bf8da8e058) —
    # a defense-in-depth layer separate from the application queries that
    # already filter explicitly by WHERE tenant_id.
    op.execute(
        "CREATE POLICY tenant_isolation ON saved_screeners "
        "USING (tenant_id::text = current_setting('app.tenant_id', true)) "
        "WITH CHECK (tenant_id::text = current_setting('app.tenant_id', true))"
    )
    op.execute("ALTER TABLE saved_screeners ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.execute("ALTER TABLE saved_screeners DISABLE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON saved_screeners")
    op.execute("DROP TABLE saved_screeners")
