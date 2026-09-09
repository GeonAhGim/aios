"""FA-0c -- ledger_account key grammar fix A: structural
(entity_id, fund_id, portfolio_id, account_type) columns instead of a
string-encoded hierarchy.

Revision ID: 18965d657219
Revises: e5a8c5d4f6b7
Create Date: 2026-09-09 00:00:00.000000

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-0c
(§9 table row 112). PM 2026-09-08 decision (task-1942) -- confirmed at
kickoff that `alembic heads` was a single head (e5a8c5d4f6b7, task-2357
R-51) and used that value as down_revision.

Problem: `ledger_account.account_code` (`4a1d0c0de005:82`) encodes the
hierarchy into a `"USER:{uuid}:{sub}" | "PLATFORM:{NAME}"` string, and that
string itself is UNIQUE. When two portfolios try to create an account of
the same `account_type`, account_code cannot distinguish the portfolios and
the insert is rejected with a UniqueViolation (§9 DoD;
`tests/integration/foundation/ledger/test_fa0c_account_scope.py`
reproduces this collision for real).

Scope of this leaf: add `entity_id`/`fund_id`/`portfolio_id` columns to
`ledger_account` and **add** a new
`UNIQUE(tenant_id, entity_id, fund_id, portfolio_id, account_type)`
constraint (the existing `UNIQUE(account_code)` stays -- see below).
`account_code` is demoted to a display-only derived value (generation is
kept, but `domain/chart_of_accounts.py`'s
`parse_account_code`/`account_type`/`allows_negative` no longer depend on
re-parsing that string).

**Why the existing `UNIQUE(account_code)` is not dropped**: `ensure_account`
(`application/purchase_flow.py`) and its call sites
(`chargeback.py`, `payouts.py`, `post_corporate_action_cash.py`,
`refund.py`, `topup.py`, `allocate_fills.py`, `legacy_wallet_bridge.py`,
`backfill.py`) all get idempotency from
`INSERT ... ON CONFLICT (account_code) DO NOTHING` -- Postgres requires the
ON CONFLICT inference target to match an actual unique/exclusion
constraint, so dropping this constraint would break every one of those
call sites on their next call with
`there is no unique or exclusion constraint matching the ON CONFLICT
specification`. This leaf does not touch those call sites (they are all
USER/PLATFORM accounts, and entity_id/fund_id/portfolio_id stay NULL for
them even after this leaf -- FA-4/FA-8 are what actually wire up
portfolio-scoped ledger writes), so keeping `UNIQUE(account_code)` and
adding the new structural constraint **alongside** it is the only safe
choice that does not break the existing regression coverage.

No FK is added: this DB has zero `legal_entity`/`fund`/`portfolio` rows yet
(FA-2 only created the tables; backfilling real users into the FA-1 default
hierarchy is a separate leaf -- `789c138f13fe` and `963d5f3cfb1b` likewise
only backfill conditionally, "only if rows already exist", for the same
reason). Adding an FK here would make the backfill below (deterministic
entity/fund/portfolio ids for platform/house accounts) fail immediately
with an FK violation.

Backfill: every one of the 5 existing rows (`account_code`) is
reverse-derived via `chart_of_accounts.default_scope()` -- this function
reuses the deterministic rules in `entities/domain/defaults.py`, anchored
on the account's own user_id for USER accounts and the house identifier
(`PLATFORM_HOUSE_USER_ID`, a fixed UUID) for PLATFORM accounts. If even one
row cannot be reverse-derived (i.e. `account_code` does not match the known
grammar), the exception is propagated as-is and fails the migration (no
silent defaulting).
"""
from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

from src.foundation.ledger.domain.chart_of_accounts import default_scope

# revision identifiers, used by Alembic.
revision: str = "18965d657219"
down_revision: str | Sequence[str] | None = "e5a8c5d4f6b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SCOPE_UNIQUE_CONSTRAINT = "ledger_account_scope_account_type_uq"


def upgrade() -> None:
    op.execute("ALTER TABLE ledger_account ADD COLUMN entity_id UUID")
    op.execute("ALTER TABLE ledger_account ADD COLUMN fund_id UUID")
    op.execute("ALTER TABLE ledger_account ADD COLUMN portfolio_id UUID")
    op.execute(
        "CREATE INDEX idx_ledger_account_portfolio_id ON ledger_account(portfolio_id) "
        "WHERE portfolio_id IS NOT NULL"
    )

    _backfill_scope()

    op.execute(
        f"ALTER TABLE ledger_account ADD CONSTRAINT {_SCOPE_UNIQUE_CONSTRAINT} "
        "UNIQUE (tenant_id, entity_id, fund_id, portfolio_id, account_type)"
    )


def _backfill_scope() -> None:
    bind = op.get_bind()
    rows = bind.execute(text("SELECT account_id, account_code FROM ledger_account")).fetchall()
    for account_id, account_code in rows:
        # Raises InvalidAccountCodeError (uncaught, on purpose) if account_code
        # does not match the known grammar — fail the migration, do not default.
        scope = default_scope(account_code)
        bind.execute(
            text(
                "UPDATE ledger_account "
                "SET entity_id = :entity_id, fund_id = :fund_id, portfolio_id = :portfolio_id "
                "WHERE account_id = :account_id"
            ),
            {
                "entity_id": scope.entity_id,
                "fund_id": scope.fund_id,
                "portfolio_id": scope.portfolio_id,
                "account_id": account_id,
            },
        )


def downgrade() -> None:
    op.execute(f"ALTER TABLE ledger_account DROP CONSTRAINT {_SCOPE_UNIQUE_CONSTRAINT}")
    op.execute("DROP INDEX IF EXISTS idx_ledger_account_portfolio_id")
    op.execute("ALTER TABLE ledger_account DROP COLUMN portfolio_id")
    op.execute("ALTER TABLE ledger_account DROP COLUMN fund_id")
    op.execute("ALTER TABLE ledger_account DROP COLUMN entity_id")
