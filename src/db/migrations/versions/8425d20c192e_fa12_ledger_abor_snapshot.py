"""FA-12 -- ledger_abor_snapshot: immutable ABOR close-marker table.

Revision ID: 8425d20c192e
Revises: 6877947783a6
Create Date: 2026-09-08 06:00:00.000000

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-12
(task-2059 decision, PM 2026-09-08).

This is an extension of the existing `src/foundation/ledger` context, not a
new one (task-2059 decision item 1) -- the table stores the one thing that
does not already exist anywhere: an immutable close-marker per
(fund_id, as_of_date), independent from the mutable `ledger_balance`
projection and from the unrelated M5 `performance_statement` reporting
context.

WORM via the shared L0-3 generator (`worm_sql`, decision item 2) -- no
bespoke trigger/REVOKE here. `balances` is a single JSONB map of
account_code -> signed net balance (decision "account_codes+balances
JSONB"), computed once at close time by
`application/ibor_view.compute_ibor_view` and never rewritten afterward
(correction is display-only, FA-12 application/ibor_view.py docstring).
`closing_recorded_at` is the `ledger_journal_entry.posted_at` cutoff that
was used for that recompute -- it doubles as "when this close considered
the ledger frozen" and as the input `application/ibor_view.correction_pending`
needs to detect later postings.

`UNIQUE (fund_id, as_of_date)` is the DoD(3) re-close guard: a second close
attempt for the same (fund_id, as_of_date) hits this constraint, and
`application/abor_snapshot.close_period` maps that `UniqueViolationError`
to a domain `AlreadyClosedError` (decision item 4) instead of letting the
raw DB error leak.
"""
from collections.abc import Sequence

from alembic import op

from src.core.db.append_only import worm_drop_sql, worm_sql

# revision identifiers, used by Alembic.
revision: str = "8425d20c192e"
down_revision: str | Sequence[str] | None = "6877947783a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "aios_app"
_TABLE = "ledger_abor_snapshot"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE {_TABLE} (
            snapshot_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            fund_id             UUID NOT NULL REFERENCES fund(fund_id),
            as_of_date          DATE NOT NULL,
            balances            JSONB NOT NULL,
            closing_recorded_at TIMESTAMPTZ NOT NULL,
            snapshot_hash       VARCHAR(64) NOT NULL,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (fund_id, as_of_date)
        )
        """
    )
    op.execute(f"GRANT SELECT, INSERT ON {_TABLE} TO {_APP_ROLE}")

    for statement in worm_sql(_TABLE):
        op.execute(statement)


def downgrade() -> None:
    for statement in worm_drop_sql(_TABLE):
        op.execute(statement)
    op.execute(f"DROP TABLE {_TABLE}")
