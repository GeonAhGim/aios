"""policy_bundle / policy_decision — CM-4 WORM enforcement retrofit.

Revision ID: c6a3d8f14b92
Revises: a1f3c9d2e5b7
Create Date: 2026-09-07 00:00:00.000000

Spec: docs/specs/L4_compliance_and_regulatory_v1.0.md#9 CM-4 (precedes CM-3
82a1206d `domain/rule_bundle.py`/`domain/evaluator.py`).

§9 CM-4 decision: "add only what's missing to the existing policy_bundle /
policy_decision (including WORM trigger verification)" — with no new table
and no new column, fill in the one thing `d8e8e4ba2365` left out when it
created the two tables: append-only enforcement. Neither table has any
column that legitimately needs to be UPDATEd after creation (`policy_bundle`
has no state machine, unlike `risk_rule_bundle` [[a9c4e1f7b2d3]], and
`policy_decision` is a purely append-only decision log, just like
`risk_decision` [[b8d5f2a1c3e4]]) — so instead of column-level guards
(the R-22 approach), this reuses L0-3 [[src/core/db/append_only.py]]
`worm_sql()`'s whole-row append-only trigger as-is on both tables (no
reimplementation, same principle as the CM-3 docstring).

Both tables were created in `d8e8e4ba2365` (a leaf far earlier than this
leaf's down_revision), before `4a1d0c0de001` (role separation), so
`ensure_roles_sql()`'s "GRANT ... ON ALL TABLES IN SCHEMA public TO
aios_app" already applied to both tables — unlike `b8d5f2a1c3e4`, which
had to add a separate GRANT for `risk_decision` (a table created
afterward), this leaf needs no separate GRANT.

Putting whole-row WORM on `policy_bundle` would trip the trigger on the
existing `postgres_policy_repository.insert_policy_bundle()`'s
`ON CONFLICT ... DO UPDATE SET mandate_revision_id =
EXCLUDED.mandate_revision_id` (an idiom that lets the loser of a
concurrent insert race get back the existing row as-is), because it
actually executes an UPDATE against itself — so together with this leaf,
the adapter is also changed to `DO NOTHING` plus a re-fetch on failure,
so that no UPDATE is ever issued (same commit, same leaf).
"""
from collections.abc import Sequence

from alembic import op

from src.core.db.append_only import worm_drop_sql, worm_sql

# revision identifiers, used by Alembic.
revision: str = "c6a3d8f14b92"
down_revision: str | Sequence[str] | None = "a1f3c9d2e5b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_WORM_TABLES = ("policy_bundle", "policy_decision")


def upgrade() -> None:
    for table in _WORM_TABLES:
        for statement in worm_sql(table):
            op.execute(statement)


def downgrade() -> None:
    for table in reversed(_WORM_TABLES):
        for statement in worm_drop_sql(table):
            op.execute(statement)
