"""performance_statement -- retrofit WORM guard trigger (task-10542 defect fix).

Revision ID: fa25b1c9d340
Revises: f1a9c6d3e8b2
Create Date: 2026-10-01 00:00:00.000000

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md §9 (L47 DoD:
"insert/query, REVOKE verification"); [[src/core/db/append_only.py]] worm_sql().

Fixes a real defect found by task-10542's DEEPEN: `performance_statement`
only received `REVOKE UPDATE, DELETE ... FROM PUBLIC` from `6e5baa1c7a55`
(2026-09-02) and was missing from `4a1d0c0de001`'s (2026-09-03, task-165
decision) WORM trigger retrofit, which was scoped to exactly three tables
(`audit_log`/`foundation_audit_event`/`wallet_transactions`). It is the same
pre-trigger-helper pattern that retrofit covered, just left out by omission
(task-10542 PM decision). REVOKE only blocks PUBLIC, not the table owner
(the migration-running account), so owner connections have been able to
UPDATE/DELETE this table and break append-only until now
(`test_gate_red_owner_update_succeeds_despite_public_revoke` reproduced the
pre-fix defect).

downgrade: same pattern as `4a1d0c0de001`'s `_WORM_RETROFIT_ALREADY_REVOKED`
-- `performance_statement` already had `REVOKE UPDATE, DELETE FROM PUBLIC`
before this leaf, so skip `worm_drop_sql()`'s last statement (re-granting
UPDATE/DELETE to PUBLIC) and only drop the trigger/guard function; running
it unchanged would over-revert past this leaf's pre-state to "WORM never
introduced".
"""

from collections.abc import Sequence

from alembic import op

from src.core.db.append_only import worm_drop_sql, worm_sql

# revision identifiers, used by Alembic.
revision: str = "fa25b1c9d340"
down_revision: str | Sequence[str] | None = "f1a9c6d3e8b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "performance_statement"


def upgrade() -> None:
    for statement in worm_sql(_TABLE):
        op.execute(statement)


def downgrade() -> None:
    for statement in worm_drop_sql(_TABLE)[:-1]:  # skip last GRANT (was already REVOKEd)
        op.execute(statement)
