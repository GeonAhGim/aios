"""policy_decision_rls -- RLS policy + FORCE for policy_decision (task-12103, F2/M).

Revision ID: d3f8a6c91e52
Revises: fa25b1c9d340
Create Date: 2026-10-15 00:00:00.000000

Spec: docs/audits/AUDIT_2026-10-15_compliance_approval.md F2(M).
task-9420/F1 fixed the GUC-binding gap for `portfolio_mandate` after
`b3c7f19ad2e6`/`c9f4e2a1b6d7` had already given it a policy+FORCE. F2 found a
one-step-earlier gap on `policy_decision`: it was never added to either
migration's table list in the first place -- `b3c7f19ad2e6`'s docstring
excuses `mandate_revision`/`policy_bundle`/`policy_decision` as "indirectly
isolated via parent FK", but `policy_decision` directly owns a `tenant_id`
column and `postgres_policy_repository.py`'s `insert_policy_decision`/
`get_cached_decision` query it by that column via a bare `self._pool.acquire()`
-- the same shape of table F1 already fixed for `portfolio_mandate`, just
missing the policy itself rather than the GUC binding. `mandate_revision`/
`policy_bundle` are out of scope here -- the audit confirms their parent-FK
indirection argument still holds for those two.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "d3f8a6c91e52"
down_revision: str | Sequence[str] | None = "fa25b1c9d340"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "policy_decision"


def upgrade() -> None:
    op.execute(
        f"CREATE POLICY tenant_isolation ON {_TABLE} "
        "USING (tenant_id::text = current_setting('app.tenant_id', true)) "
        "WITH CHECK (tenant_id::text = current_setting('app.tenant_id', true))"
    )
    op.execute(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.execute(f"ALTER TABLE {_TABLE} NO FORCE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {_TABLE} DISABLE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {_TABLE}")
