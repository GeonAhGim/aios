"""approval_requests_tenant_rls -- tenant_id column + backfill + RLS policy +
FORCE for `approval_requests` (task-12104, F2/M).

Revision ID: a4f27d91c6e8
Revises: d3f8a6c91e52
Create Date: 2026-10-15 06:00:00.000000

Spec: docs/audits/AUDIT_2026-10-15_compliance_approval.md F2(M),
docs/specs/L4_platform_observability_tenancy_api_v1.0.md §9 PLT-30.

`189bdd25a017`'s `approval_requests` owns only `user_id` (NULL for
scope='PLATFORM'), never `tenant_id` -- it is in neither
`b3c7f19ad2e6`'s foundation-8 list nor its legacy-3 policy-only list, so it
has had zero RLS protection since creation. PM decision (task-12104,
2026-10-15): "user_id == tenant_id" is not trustworthy as a bare
assumption, so this migration does not just copy the column -- it adds a
real `tenant_id` column and backfills it from the actual `tenant` table
(PERSONAL tenant's `id == user_id` invariant, PLT-26 `f4a6b8c0d2e4`/
`b8ac30eb4fe0`), i.e. a row only gets a `tenant_id` when a matching
`tenant` row is independently confirmed to exist for its `user_id`.

scope='PLATFORM' rows have no `user_id` and so get no `tenant_id` by
design (not user-owned) -- same NULL-under-system-role exception shape
`b3c7f19ad2e6` already uses for `foundation_audit_event`. A USER-scope row
whose `user_id` has no matching `tenant` row (should not occur given the
PLT-26/28 backfill invariant) also keeps `tenant_id IS NULL` and falls
under the same system-only exception rather than being guessed.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "a4f27d91c6e8"
down_revision: str | Sequence[str] | None = "d3f8a6c91e52"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "approval_requests"

_BACKFILL_SQL = """
    UPDATE approval_requests ar
    SET tenant_id = t.id
    FROM tenant t
    WHERE t.id = ar.user_id
      AND ar.tenant_id IS NULL
      AND ar.user_id IS NOT NULL
"""

_POLICY_PREDICATE = (
    "(tenant_id::text = current_setting('app.tenant_id', true)) OR "
    "(tenant_id IS NULL AND current_setting('app.role', true) = 'system')"
)


def upgrade() -> None:
    op.execute(f"ALTER TABLE {_TABLE} ADD COLUMN tenant_id UUID")
    op.execute(_BACKFILL_SQL)
    op.execute(f"CREATE INDEX idx_approval_requests_tenant ON {_TABLE}(tenant_id)")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {_TABLE} "
        f"USING ({_POLICY_PREDICATE}) WITH CHECK ({_POLICY_PREDICATE})"
    )
    op.execute(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.execute(f"ALTER TABLE {_TABLE} NO FORCE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {_TABLE} DISABLE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {_TABLE}")
    op.execute("DROP INDEX IF EXISTS idx_approval_requests_tenant")
    op.execute(f"ALTER TABLE {_TABLE} DROP COLUMN tenant_id")
