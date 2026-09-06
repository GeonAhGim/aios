"""FA-8 - `fill_allocation` table: persists fills -> sub_account allocation results.

Revision ID: 4102098cbd0f
Revises: 963d5f3cfb1b
Create Date: 2026-09-07 00:00:00.000000

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-8
(table in §9, allocation row in §2.1, FA-A3 in §4).

This table holds the actual fact ("fills -> sub_account allocation":
quantity, average_price) - it is not the "bespoke duplicate-check table"
the decision (task-1796) forbids (that would mean an auxiliary table whose
sole purpose is an idempotency lookup; idempotency here reuses LC-3's
`post_entry` `idempotency_key` as-is - see `application/allocate_fills.py`).
`UNIQUE(order_id, sub_account_id)` is an integrity constraint on this fact
record itself (the same sub_account of the same order is allocated at
most once), not a separate dedup table.

Append-only for the same reason as `fills`/`pos_journal`/
`ledger_journal_entry`: an allocation is recorded after the fact is
settled, so corrections use a new row (FA-11's reversal pattern) rather
than an UPDATE of the existing one (`core/db/append_only.worm_sql` is
reused; no new WORM implementation).

No RLS: both `order_id` (-> `orders`) and `sub_account_id` (->
`sub_account`) already isolate this table indirectly via FK (same
principle as 789c138f13fe/963d5f3cfb1b - a child table indirectly isolated
through its parent is exempt from RLS).
"""
from collections.abc import Sequence

from alembic import op

from src.core.db.append_only import worm_drop_sql, worm_sql

# revision identifiers, used by Alembic.
revision: str = "4102098cbd0f"
down_revision: str | Sequence[str] | None = "963d5f3cfb1b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "fill_allocation"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE {_TABLE} (
            id              BIGSERIAL PRIMARY KEY,
            order_id        UUID NOT NULL REFERENCES orders(order_id),
            sub_account_id  UUID NOT NULL REFERENCES sub_account(sub_account_id),
            tenant_id       UUID NOT NULL,
            fund_id         UUID NOT NULL REFERENCES fund(fund_id),
            portfolio_id    UUID NOT NULL REFERENCES portfolio(portfolio_id),
            quantity        NUMERIC(30,10) NOT NULL CHECK (quantity > 0),
            average_price   NUMERIC(30,10) NOT NULL CHECK (average_price > 0),
            ledger_entry_id UUID NOT NULL REFERENCES ledger_journal_entry(entry_id),
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (order_id, sub_account_id)
        )
        """
    )
    op.execute(f"CREATE INDEX idx_{_TABLE}_sub_account_id ON {_TABLE}(sub_account_id)")
    op.execute(f"CREATE INDEX idx_{_TABLE}_fund_id ON {_TABLE}(fund_id)")
    for statement in worm_sql(_TABLE):
        op.execute(statement)


def downgrade() -> None:
    for statement in worm_drop_sql(_TABLE):
        op.execute(statement)
    op.execute(f"DROP INDEX IF EXISTS idx_{_TABLE}_fund_id")
    op.execute(f"DROP INDEX IF EXISTS idx_{_TABLE}_sub_account_id")
    op.execute(f"DROP TABLE IF EXISTS {_TABLE}")
