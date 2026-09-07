"""FA-10 -- retroactive migration C: bitemporal columns + no-UPDATE trigger
on projection tables.

Revision ID: a2c4f9e1b3d5
Revises: a1f3c9d2e5b7
Create Date: 2026-09-07 06:00:00.000000

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-10
(SS2.3, SS3, SS4 FA-A2, SS9 FA-10). Depends on FA-9 (`core/bitemporal.py`,
task-1702) and FA-4 (`963d5f3cfb1b`), both already merged.

Scope (SS2.3 2026-09-06 audit reduction): tables that already have a WORM
trigger and are therefore already physically UPDATE-proof (`pos_journal`,
`ledger_journal_entry`, `ledger_posting_line`, `order_events`, `fills`,
`risk_decision`) are left alone -- adding tx_from/tx_to there would be a
dead column (SS2.3). Only the three projection tables that are still
mutated in place are in scope: `pos_snapshot`, `ledger_balance`, legacy
`positions`.

`md_symbol_alias`'s existing SCD-2 (`4a1d0c0de007`, valid_from/valid_to +
EXCLUDE, closed via UPDATE) is left untouched (task-2051 decision) -- it is
out of this leaf's scope, and the PM judged that changing how an
already-loaded market-data reference table closes its intervals carries
more re-ingestion risk than benefit.

Design: `valid_from`/`tx_from` are both stamped with now() (this leaf does
not yet distinguish "when a fact became true" from "when we learned it" --
that distinction is FA-11/12's correction/restatement domain). `valid_to`/
`tx_to` stay NULL forever in this leaf -- write paths implement "append a
new version" by DELETE (old row) + INSERT (replacement row, same natural
key) inside one transaction instead of UPDATE (FA-A2: "state tables forbid
UPDATE, correction is a new row" -- literally, no UPDATE statement is ever
executed). The `_current` views expose the `tx_to IS NULL` filter DoD(3)
asks for -- today every row's tx_to is NULL so it is a pass-through, but
once FA-11/12 start accumulating real corrections this filter becomes the
"current" test.

The two FKs that point at `positions(id)` (`pos_snapshot.legacy_position_id`,
`reconciliation_events.position_id`) are made DEFERRABLE INITIALLY DEFERRED
so that `positions`'s replace-in-place write pattern (DELETE id=X, then
INSERT the same id=X, inside one transaction) does not trip a FK violation
at the DELETE step -- Postgres only re-checks a deferred constraint at
COMMIT, by which time a row with that id exists again.
"""
from collections.abc import Sequence

from alembic import op

from src.core.db.no_update_guard import no_update_guard_drop_sql, no_update_guard_sql

# revision identifiers, used by Alembic.
revision: str = "a2c4f9e1b3d5"
down_revision: str | Sequence[str] | None = "a1f3c9d2e5b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "aios_app"
_TABLES = ("pos_snapshot", "ledger_balance", "positions")

# (table, constraint_name) -- actual auto-generated names, confirmed via information_schema.
_DEFERRED_FKS = (
    ("pos_snapshot", "pos_snapshot_legacy_position_id_fkey"),
    ("reconciliation_events", "reconciliation_events_position_id_fkey"),
)


def upgrade() -> None:
    for table in _TABLES:
        op.execute(
            f"""
            ALTER TABLE {table}
                ADD COLUMN valid_from TIMESTAMPTZ NOT NULL DEFAULT now(),
                ADD COLUMN valid_to   TIMESTAMPTZ,
                ADD COLUMN tx_from    TIMESTAMPTZ NOT NULL DEFAULT now(),
                ADD COLUMN tx_to      TIMESTAMPTZ
            """
        )
        for statement in no_update_guard_sql(table):
            op.execute(statement)
        op.execute(
            f"CREATE VIEW {table}_current AS SELECT * FROM {table} WHERE tx_to IS NULL"  # noqa: S608
        )
        op.execute(f"GRANT SELECT ON {table}_current TO {_APP_ROLE}")

    for table, constraint in _DEFERRED_FKS:
        op.execute(
            f"ALTER TABLE {table} ALTER CONSTRAINT {constraint} DEFERRABLE INITIALLY DEFERRED"
        )


def downgrade() -> None:
    for table, constraint in _DEFERRED_FKS:
        op.execute(f"ALTER TABLE {table} ALTER CONSTRAINT {constraint} NOT DEFERRABLE")

    for table in reversed(_TABLES):
        op.execute(f"DROP VIEW {table}_current")
        for statement in no_update_guard_drop_sql(table):
            op.execute(statement)
        op.execute(
            f"""
            ALTER TABLE {table}
                DROP COLUMN valid_from,
                DROP COLUMN valid_to,
                DROP COLUMN tx_from,
                DROP COLUMN tx_to
            """
        )
