"""FA-0a batch A — move tenant_id FK from users to tenant for 7 foundation files.

Revision ID: ccfb229d760d
Revises: b5bf8da8e058
Create Date: 2026-09-07 00:00:00.000000

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md #9 FA-0a,
ADR-2026-09-06-E/G D0. task-1814 decision — of the 19 legacy tables, this
leaf corrects **batch A only (7 foundation files, 9 tables)**:
4453afe74725 audit_event (foundation_audit_event), a1f3c9d6b8e2
connections (account_connection), e91a4c2b7d63
paper_control (paper_deployment), 6e5baa1c7a55
performance (valuation_snapshot, performance_statement), f2b8e5d1a734
reconciliation (reconciliation_run, reconciliation_state), c7d4e1a9f052
risk_gate (risk_evaluation), 84b7d0faf14f trust (consent_record).
Ledger/positions/market-data (batch B) go to task-1987; risk/portfolio/
entities plus the audit-baseline retirement (batch C) go to task-1988.

The correction pattern follows a0e7e1454b60 (FA-2a
legal_entity_tenant_fk_fix) exactly: DROP -> backfill only the rows whose
invariant is broken to the first tenant -> ADD. The real tenant table is
`tenant(id)` from `f4a6b8c0d2e4`, and that revision already created a
PERSONAL tenant with `id = user_id` for every existing user, so any
tenant_id value that used to satisfy `users(user_id)` is already a valid
`tenant(id)` value -- the normal case needs no value change.
`foundation_audit_event.tenant_id` allows NULL for system events
(4453afe74725 #1), so it is naturally excluded from the backfill (NULL
evaluates to NULL in `NOT IN (...)`, which never matches the WHERE clause).

Each ALTER TABLE statement is spelled out per table instead of looping
over a list -- `scripts/check_audit_regressions.py`'s tenant_fk_to_users
check still finds the file that wrote the original CREATE TABLE by
scanning literal text (a correction hidden behind an f-string loop
variable can't be correlated back to it). Along with this file's
`_fk_fix` suffix, the literal `ALTER TABLE <table> ADD CONSTRAINT ...
REFERENCES tenant(` text lets the checker recognize that a table has
already been corrected and stop counting the stale definition in the
original file as an open defect (see check_audit_regressions.py).
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ccfb229d760d"
down_revision: str | Sequence[str] | None = "b5bf8da8e058"
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
    op.execute(
        "ALTER TABLE foundation_audit_event DROP CONSTRAINT "
        "foundation_audit_event_tenant_id_fkey"
    )
    _backfill("foundation_audit_event")
    op.execute(
        "ALTER TABLE foundation_audit_event ADD CONSTRAINT foundation_audit_event_tenant_id_fkey "
        "FOREIGN KEY (tenant_id) REFERENCES tenant(id)"
    )

    op.execute("ALTER TABLE account_connection DROP CONSTRAINT account_connection_tenant_id_fkey")
    _backfill("account_connection")
    op.execute(
        "ALTER TABLE account_connection ADD CONSTRAINT account_connection_tenant_id_fkey "
        "FOREIGN KEY (tenant_id) REFERENCES tenant(id)"
    )

    op.execute("ALTER TABLE paper_deployment DROP CONSTRAINT paper_deployment_tenant_id_fkey")
    _backfill("paper_deployment")
    op.execute(
        "ALTER TABLE paper_deployment ADD CONSTRAINT paper_deployment_tenant_id_fkey "
        "FOREIGN KEY (tenant_id) REFERENCES tenant(id)"
    )

    op.execute("ALTER TABLE valuation_snapshot DROP CONSTRAINT valuation_snapshot_tenant_id_fkey")
    _backfill("valuation_snapshot")
    op.execute(
        "ALTER TABLE valuation_snapshot ADD CONSTRAINT valuation_snapshot_tenant_id_fkey "
        "FOREIGN KEY (tenant_id) REFERENCES tenant(id)"
    )

    op.execute(
        "ALTER TABLE performance_statement DROP CONSTRAINT performance_statement_tenant_id_fkey"
    )
    _backfill("performance_statement")
    op.execute(
        "ALTER TABLE performance_statement ADD CONSTRAINT performance_statement_tenant_id_fkey "
        "FOREIGN KEY (tenant_id) REFERENCES tenant(id)"
    )

    op.execute(
        "ALTER TABLE reconciliation_run DROP CONSTRAINT reconciliation_run_tenant_id_fkey"
    )
    _backfill("reconciliation_run")
    op.execute(
        "ALTER TABLE reconciliation_run ADD CONSTRAINT reconciliation_run_tenant_id_fkey "
        "FOREIGN KEY (tenant_id) REFERENCES tenant(id)"
    )

    op.execute(
        "ALTER TABLE reconciliation_state DROP CONSTRAINT reconciliation_state_tenant_id_fkey"
    )
    _backfill("reconciliation_state")
    op.execute(
        "ALTER TABLE reconciliation_state ADD CONSTRAINT reconciliation_state_tenant_id_fkey "
        "FOREIGN KEY (tenant_id) REFERENCES tenant(id)"
    )

    op.execute("ALTER TABLE risk_evaluation DROP CONSTRAINT risk_evaluation_tenant_id_fkey")
    _backfill("risk_evaluation")
    op.execute(
        "ALTER TABLE risk_evaluation ADD CONSTRAINT risk_evaluation_tenant_id_fkey "
        "FOREIGN KEY (tenant_id) REFERENCES tenant(id)"
    )

    op.execute("ALTER TABLE consent_record DROP CONSTRAINT consent_record_tenant_id_fkey")
    _backfill("consent_record")
    op.execute(
        "ALTER TABLE consent_record ADD CONSTRAINT consent_record_tenant_id_fkey "
        "FOREIGN KEY (tenant_id) REFERENCES tenant(id)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE consent_record DROP CONSTRAINT consent_record_tenant_id_fkey")
    op.execute(
        "ALTER TABLE consent_record ADD CONSTRAINT consent_record_tenant_id_fkey "
        "FOREIGN KEY (tenant_id) REFERENCES users(user_id)"
    )

    op.execute("ALTER TABLE risk_evaluation DROP CONSTRAINT risk_evaluation_tenant_id_fkey")
    op.execute(
        "ALTER TABLE risk_evaluation ADD CONSTRAINT risk_evaluation_tenant_id_fkey "
        "FOREIGN KEY (tenant_id) REFERENCES users(user_id)"
    )

    op.execute(
        "ALTER TABLE reconciliation_state DROP CONSTRAINT reconciliation_state_tenant_id_fkey"
    )
    op.execute(
        "ALTER TABLE reconciliation_state ADD CONSTRAINT reconciliation_state_tenant_id_fkey "
        "FOREIGN KEY (tenant_id) REFERENCES users(user_id)"
    )

    op.execute(
        "ALTER TABLE reconciliation_run DROP CONSTRAINT reconciliation_run_tenant_id_fkey"
    )
    op.execute(
        "ALTER TABLE reconciliation_run ADD CONSTRAINT reconciliation_run_tenant_id_fkey "
        "FOREIGN KEY (tenant_id) REFERENCES users(user_id)"
    )

    op.execute(
        "ALTER TABLE performance_statement DROP CONSTRAINT performance_statement_tenant_id_fkey"
    )
    op.execute(
        "ALTER TABLE performance_statement ADD CONSTRAINT performance_statement_tenant_id_fkey "
        "FOREIGN KEY (tenant_id) REFERENCES users(user_id)"
    )

    op.execute("ALTER TABLE valuation_snapshot DROP CONSTRAINT valuation_snapshot_tenant_id_fkey")
    op.execute(
        "ALTER TABLE valuation_snapshot ADD CONSTRAINT valuation_snapshot_tenant_id_fkey "
        "FOREIGN KEY (tenant_id) REFERENCES users(user_id)"
    )

    op.execute("ALTER TABLE paper_deployment DROP CONSTRAINT paper_deployment_tenant_id_fkey")
    op.execute(
        "ALTER TABLE paper_deployment ADD CONSTRAINT paper_deployment_tenant_id_fkey "
        "FOREIGN KEY (tenant_id) REFERENCES users(user_id)"
    )

    op.execute("ALTER TABLE account_connection DROP CONSTRAINT account_connection_tenant_id_fkey")
    op.execute(
        "ALTER TABLE account_connection ADD CONSTRAINT account_connection_tenant_id_fkey "
        "FOREIGN KEY (tenant_id) REFERENCES users(user_id)"
    )

    op.execute(
        "ALTER TABLE foundation_audit_event DROP CONSTRAINT "
        "foundation_audit_event_tenant_id_fkey"
    )
    op.execute(
        "ALTER TABLE foundation_audit_event ADD CONSTRAINT foundation_audit_event_tenant_id_fkey "
        "FOREIGN KEY (tenant_id) REFERENCES users(user_id)"
    )
