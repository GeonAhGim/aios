"""LA-25 — create pos_borrow_position (short-sale borrow/margin
persistence).

Revision ID: ddbd3a33bf30
Revises: 47ec4b178f54
Create Date: 2026-09-07 01:00:00.000000

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md §9 LA-25,
ADR-2026-09-06-G §9. The table that persists the `BorrowPosition` value
object from `src/foundation/positions/domain/borrow.py` (task-1752's first
leaf, e31a63b). As that leaf's docstring foretold, this leaf builds only
the physical schema — wiring the repository adapter and the order-path
locate gate is a later leaf.

Parent decision (task-1752 decision): confirmed at start of work that
`alembic heads` was single (`47ec4b178f54`) and used it as `down_revision`
as-is. FA-0a batch B (task-1987, converting the positions family's
tenant_id FK from users to tenant) is still in progress, so
`pos_account`/`pos_snapshot` still carry the legacy `tenant_id` FK'd to
`users(user_id)` -- this table does not carry that legacy forward and,
being a brand-new table, references the correct target `tenant(id)` from
the start (correcting the legacy is not this leaf's job).
`position_key` does not FK `pos_snapshot(position_key)` — combining the two
tables' tenant concepts while batch B is still in progress would couple
this table to batch B's completion order, so the linkage is instead
verified at the application level in the repository adapter leaf.

The `tenant_id`/`portfolio_id` column layout keeps `portfolio_id` nullable,
the same way FA-4 (963d5f3cfb1b) added `portfolio_id` to
`pos_account`/`pos_snapshot` (bootstrap data does not exist for every user,
same reason). RLS reuses the same `tenant_isolation` policy shape as PLT-30
M5 (b3c7f19ad2e6) — being a brand-new table there is no reason to defer
ENABLE the way legacy tables did, so it is enabled immediately (same
judgment as CH-5 e1d9b5ed8d7d).
"""

from collections.abc import Sequence

from alembic import op

from src.data.models.base import Currency

revision: str = "ddbd3a33bf30"
down_revision: str | Sequence[str] | None = "47ec4b178f54"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "aios_app"
_TABLE = "pos_borrow_position"


def _sql_enum_members() -> str:
    return ", ".join(f"'{member.value}'" for member in Currency)


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE {_TABLE} (
            position_key    VARCHAR(200) PRIMARY KEY,
            tenant_id       UUID NOT NULL REFERENCES tenant(id),
            portfolio_id    UUID REFERENCES portfolio(portfolio_id),
            short_quantity  NUMERIC(30,10) NOT NULL CHECK (short_quantity > 0),
            supply_rate     NUMERIC(20,10) NOT NULL CHECK (supply_rate >= 0),
            currency        VARCHAR(10) NOT NULL
                CHECK (currency IN ({_sql_enum_members()})),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(f"CREATE INDEX idx_{_TABLE}_tenant_id ON {_TABLE}(tenant_id)")
    op.execute(
        f"CREATE INDEX idx_{_TABLE}_portfolio_id ON {_TABLE}(portfolio_id) "
        "WHERE portfolio_id IS NOT NULL"
    )

    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {_TABLE} TO {_APP_ROLE}")

    op.execute(
        f"CREATE POLICY tenant_isolation ON {_TABLE} "
        "USING (tenant_id::text = current_setting('app.tenant_id', true)) "
        "WITH CHECK (tenant_id::text = current_setting('app.tenant_id', true))"
    )
    op.execute(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.execute(f"ALTER TABLE {_TABLE} DISABLE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {_TABLE}")
    op.execute(f"DROP TABLE {_TABLE}")
