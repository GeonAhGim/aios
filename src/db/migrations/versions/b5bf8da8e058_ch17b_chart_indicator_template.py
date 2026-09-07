"""ch17b_chart_indicator_template — CH-17b

Revision ID: b5bf8da8e058
Revises: 4102098cbd0f
Create Date: 2026-09-07 05:05:18.645584

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §2.2,
§9.11 CH-17. `chart_indicator_template` follows the same tenant top-level
table pattern as CH-5's `chart_layout` (e1d9b5ed8d7d) — two differences:
(1) `tenant_id` FKs `tenant(id)` (ADR-2026-09-06-G §2 D0 — prevents a
repeat of the incident where `users` was FK'd by mistake; `chart_layout`
lacks this FK, so it is corrected fresh here), (2) `UNIQUE(tenant_id, name)`
blocks duplicate names within the same tenant at the schema level (leaves
no race for an application-layer duplicate check, same principle as
standard 105 §2.2).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "b5bf8da8e058"
down_revision: str | Sequence[str] | None = "4102098cbd0f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "aios_app"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE chart_indicator_template (
            id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id        UUID NOT NULL REFERENCES tenant(id),
            owner_subject_id UUID NOT NULL,
            name             VARCHAR(120) NOT NULL,
            template         JSONB NOT NULL,
            revision         INTEGER NOT NULL DEFAULT 0,
            created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_chart_indicator_template_tenant_name UNIQUE (tenant_id, name)
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_chart_indicator_template_tenant "
        "ON chart_indicator_template(tenant_id)"
    )

    op.execute(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON chart_indicator_template TO {_APP_ROLE}"
    )

    # Same tenant_isolation policy as PLT-30 M5 (b3c7f19ad2e6) / CH-5 (e1d9b5ed8d7d).
    op.execute(
        "CREATE POLICY tenant_isolation ON chart_indicator_template "
        "USING (tenant_id::text = current_setting('app.tenant_id', true)) "
        "WITH CHECK (tenant_id::text = current_setting('app.tenant_id', true))"
    )
    op.execute("ALTER TABLE chart_indicator_template ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.execute("ALTER TABLE chart_indicator_template DISABLE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON chart_indicator_template")
    op.execute("DROP TABLE chart_indicator_template")
