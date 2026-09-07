"""FA-0a batch C — move tenant_id FK from users to tenant for risk/portfolio/entities.

Revision ID: 94da854f522f
Revises: c6a3d8f14b92
Create Date: 2026-09-07 06:00:00.000000

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md #9 FA-0a,
ADR-2026-09-06-E/G D0. task-1814 split the 19-table defect into three
batches; batch A (7 foundation files) landed via ccfb229d760d, batch B
(4 files, ledger/positions/market-data) via f6b25409405e. This leaf
corrects **batch C, the last of FA-0a**: 4 files, 5 locations --
b8d5f2a1c3e4 risk_decision_worm (risk_decision), c7e6a3b2d4f5 risk_limit
(risk_limit), d8e8e4ba2365 portfolio_mandate_policy (portfolio_mandate,
policy_decision). The fifth location, e6b1d94a7c3f's `legal_entity`, was
already corrected separately and earlier by a0e7e1454b60 (FA-2a) -- it
needed no new ALTER here, but its closure is why this batch can retire
the audit-baseline entry (see audit-baseline.json diff in this same
commit).

The correction pattern follows a0e7e1454b60 (FA-2a
legal_entity_tenant_fk_fix), ccfb229d760d (batch A), and f6b25409405e
(batch B) exactly: DROP -> backfill only the rows whose invariant is
broken to the first tenant -> ADD. The real tenant table is `tenant(id)`
from `f4a6b8c0d2e4`, and that revision already created a PERSONAL tenant
with `id = user_id` for every existing user, so any tenant_id value that
used to satisfy `users(user_id)` is already a valid `tenant(id)` value --
the normal case needs no value change. `risk_limit.tenant_id` is
nullable (no NOT NULL in its CREATE TABLE), so it is naturally excluded
from the backfill the same way `foundation_audit_event.tenant_id` was in
batch A (NULL evaluates to NULL in `NOT IN (...)`, which never matches
the WHERE clause).

Each ALTER TABLE statement is spelled out per table instead of looping
over a list -- `scripts/check_audit_regressions.py`'s tenant_fk_to_users
check still finds the file that wrote the original CREATE TABLE by
scanning literal text. Along with this file's `_fk_fix` suffix, the
literal `ALTER TABLE <table> ADD CONSTRAINT ... REFERENCES tenant(` text
lets the checker recognize that a table has already been corrected and
stop counting the stale definition in the original file as an open
defect (see check_audit_regressions.py).
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "94da854f522f"
down_revision: str | Sequence[str] | None = "c6a3d8f14b92"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _backfill(table: str) -> None:
    op.execute(
        f"""
        UPDATE {table}
        SET tenant_id = (SELECT id FROM tenant ORDER BY created_at LIMIT 1)
        WHERE tenant_id NOT IN (SELECT id FROM tenant)
        AND EXISTS (SELECT 1 FROM tenant)
        """
    )


def upgrade() -> None:
    op.execute("ALTER TABLE risk_decision DROP CONSTRAINT risk_decision_tenant_id_fkey")
    _backfill("risk_decision")
    op.execute(
        "ALTER TABLE risk_decision ADD CONSTRAINT risk_decision_tenant_id_fkey "
        "FOREIGN KEY (tenant_id) REFERENCES tenant(id)"
    )

    op.execute("ALTER TABLE risk_limit DROP CONSTRAINT risk_limit_tenant_id_fkey")
    _backfill("risk_limit")
    op.execute(
        "ALTER TABLE risk_limit ADD CONSTRAINT risk_limit_tenant_id_fkey "
        "FOREIGN KEY (tenant_id) REFERENCES tenant(id)"
    )

    op.execute(
        "ALTER TABLE portfolio_mandate DROP CONSTRAINT portfolio_mandate_tenant_id_fkey"
    )
    _backfill("portfolio_mandate")
    op.execute(
        "ALTER TABLE portfolio_mandate ADD CONSTRAINT portfolio_mandate_tenant_id_fkey "
        "FOREIGN KEY (tenant_id) REFERENCES tenant(id)"
    )

    op.execute("ALTER TABLE policy_decision DROP CONSTRAINT policy_decision_tenant_id_fkey")
    _backfill("policy_decision")
    op.execute(
        "ALTER TABLE policy_decision ADD CONSTRAINT policy_decision_tenant_id_fkey "
        "FOREIGN KEY (tenant_id) REFERENCES tenant(id)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE policy_decision DROP CONSTRAINT policy_decision_tenant_id_fkey")
    op.execute(
        "ALTER TABLE policy_decision ADD CONSTRAINT policy_decision_tenant_id_fkey "
        "FOREIGN KEY (tenant_id) REFERENCES users(user_id)"
    )

    op.execute(
        "ALTER TABLE portfolio_mandate DROP CONSTRAINT portfolio_mandate_tenant_id_fkey"
    )
    op.execute(
        "ALTER TABLE portfolio_mandate ADD CONSTRAINT portfolio_mandate_tenant_id_fkey "
        "FOREIGN KEY (tenant_id) REFERENCES users(user_id)"
    )

    op.execute("ALTER TABLE risk_limit DROP CONSTRAINT risk_limit_tenant_id_fkey")
    op.execute(
        "ALTER TABLE risk_limit ADD CONSTRAINT risk_limit_tenant_id_fkey "
        "FOREIGN KEY (tenant_id) REFERENCES users(user_id)"
    )

    op.execute("ALTER TABLE risk_decision DROP CONSTRAINT risk_decision_tenant_id_fkey")
    op.execute(
        "ALTER TABLE risk_decision ADD CONSTRAINT risk_decision_tenant_id_fkey "
        "FOREIGN KEY (tenant_id) REFERENCES users(user_id)"
    )
