"""ux7_shared_screeners_screen_alerts — UX-7 share/conditional-alert storage

Revision ID: 2e97e28fe878
Revises: 7a2523bc3e5a
Create Date: 2026-09-22 00:00:00.000000

Spec: docs/specs/L4_product_experience_and_discovery_v1.0.md §2.2
`application/{save_screen,share_screen,alert_on_screen}.py` (save / share
[market rules] / conditional alert), §9 UX-7 DoD. `save_screen` reuses the
UX-5 `saved_screeners` table (052b26dfb97b) as-is — no schema change there.

`shared_screeners` implements the MP-3 immutable-version invariant
which is still `hold` per
docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §9.7 — each
share INSERTs the next `version` for `screener_id`, existing rows are never
UPDATEd, so a past share stays frozen even if the source `saved_screeners`
row is edited later. Unlike `saved_screeners` (strict per-tenant RLS), the
whole point of "share" is cross-tenant visibility, so RLS here allows SELECT
to every tenant but restricts INSERT to the owning tenant — there is no
UPDATE/DELETE policy because the table is insert-only.

`screen_alerts` mirrors `price_alerts`(a1b2c3d4e5f6, FD-14)'s status/
threshold shape and deliberately carries **no RLS**, same precedent as that
table: the evaluation loop (`application/alert_on_screen.py::list_active`,
mirroring `AlertService.evaluate_all_active`) reads ACTIVE alerts across
every tenant outside of any `app.tenant_id` transaction scope (PLT-30,
`src/core/db/tenant_scope.py`), so a `tenant_isolation` policy would make
that read return 0 rows. The condition compared is the screen's matched-row
count (`application/run_screen.py::ScreenRunPage.total`) against
`threshold` by `operator`, not a single indicator value.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "2e97e28fe878"
down_revision: str | Sequence[str] | None = "7a2523bc3e5a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "aios_app"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE shared_screeners (
            id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            screener_id  UUID NOT NULL REFERENCES saved_screeners(id) ON DELETE CASCADE,
            tenant_id    UUID NOT NULL REFERENCES tenant(id),
            name         VARCHAR(120) NOT NULL,
            definition   JSONB NOT NULL,
            version      INTEGER NOT NULL,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_shared_screeners_screener_version UNIQUE (screener_id, version)
        )
        """
    )
    op.execute("CREATE INDEX idx_shared_screeners_screener ON shared_screeners(screener_id)")

    op.execute(f"GRANT SELECT, INSERT ON shared_screeners TO {_APP_ROLE}")

    op.execute("CREATE POLICY shared_screeners_read ON shared_screeners FOR SELECT USING (true)")
    op.execute(
        "CREATE POLICY shared_screeners_write ON shared_screeners FOR INSERT "
        "WITH CHECK (tenant_id::text = current_setting('app.tenant_id', true))"
    )
    op.execute("ALTER TABLE shared_screeners ENABLE ROW LEVEL SECURITY")

    op.execute(
        """
        CREATE TABLE screen_alerts (
            id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id        UUID NOT NULL REFERENCES tenant(id),
            screener_id      UUID NOT NULL REFERENCES saved_screeners(id) ON DELETE CASCADE,
            operator         VARCHAR(10) NOT NULL
                CHECK (operator IN ('gte', 'gt', 'lte', 'lt', 'eq')),
            threshold        INTEGER NOT NULL,
            status           VARCHAR(20) NOT NULL DEFAULT 'ACTIVE'
                CHECK (status IN ('ACTIVE', 'TRIGGERED', 'CANCELLED')),
            created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
            triggered_at     TIMESTAMPTZ,
            triggered_count  INTEGER
        )
        """
    )
    op.execute("CREATE INDEX idx_screen_alerts_tenant ON screen_alerts(tenant_id)")
    op.execute(
        "CREATE INDEX idx_screen_alerts_active ON screen_alerts(status) WHERE status = 'ACTIVE'"
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON screen_alerts TO {_APP_ROLE}")


def downgrade() -> None:
    op.execute("DROP TABLE screen_alerts")

    op.execute("ALTER TABLE shared_screeners DISABLE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS shared_screeners_write ON shared_screeners")
    op.execute("DROP POLICY IF EXISTS shared_screeners_read ON shared_screeners")
    op.execute("DROP TABLE shared_screeners")
