"""FA-0a batch B — move tenant_id FK from users to tenant for ledger/positions/market-data.

Revision ID: f6b25409405e
Revises: 3819cf8a5373
Create Date: 2026-09-07 05:00:00.000000

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md #9 FA-0a,
ADR-2026-09-06-E/G D0. task-1814 split the 19-table defect into three
batches; batch A (7 foundation files) landed via ccfb229d760d. This leaf
corrects **batch B only (4 files, 6 columns)**: 4a1d0c0de004
positions_journal (pos_account, pos_journal, pos_snapshot), 4a1d0c0de005
ledger_core (ledger_account), 4a1d0c0de008 md_candles (md_ingest_batch),
4a1d0c0de009 md_ingest_batch_tick (md_ingest_batch_tick). Risk/portfolio/
entities plus the audit-baseline retirement (batch C) go to task-1988.

The correction pattern follows a0e7e1454b60 (FA-2a
legal_entity_tenant_fk_fix) and ccfb229d760d (FA-0a batch A) exactly:
DROP -> backfill only the rows whose invariant is broken to the first
tenant -> ADD. The real tenant table is `tenant(id)` from `f4a6b8c0d2e4`,
and that revision already created a PERSONAL tenant with `id = user_id`
for every existing user, so any tenant_id value that used to satisfy
`users(user_id)` is already a valid `tenant(id)` value -- the normal case
needs no value change. `ledger_account.tenant_id`, `md_ingest_batch.
tenant_id`, and `md_ingest_batch_tick.tenant_id` are all nullable (no
NOT NULL in their CREATE TABLE), so they are naturally excluded from the
backfill the same way `foundation_audit_event.tenant_id` was in batch A
(NULL evaluates to NULL in `NOT IN (...)`, which never matches the WHERE
clause).

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
revision: str = "f6b25409405e"
down_revision: str | Sequence[str] | None = "3819cf8a5373"
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
    op.execute("ALTER TABLE pos_account DROP CONSTRAINT pos_account_tenant_id_fkey")
    _backfill("pos_account")
    op.execute(
        "ALTER TABLE pos_account ADD CONSTRAINT pos_account_tenant_id_fkey "
        "FOREIGN KEY (tenant_id) REFERENCES tenant(id)"
    )

    op.execute("ALTER TABLE pos_journal DROP CONSTRAINT pos_journal_tenant_id_fkey")
    _backfill("pos_journal")
    op.execute(
        "ALTER TABLE pos_journal ADD CONSTRAINT pos_journal_tenant_id_fkey "
        "FOREIGN KEY (tenant_id) REFERENCES tenant(id)"
    )

    op.execute("ALTER TABLE pos_snapshot DROP CONSTRAINT pos_snapshot_tenant_id_fkey")
    _backfill("pos_snapshot")
    op.execute(
        "ALTER TABLE pos_snapshot ADD CONSTRAINT pos_snapshot_tenant_id_fkey "
        "FOREIGN KEY (tenant_id) REFERENCES tenant(id)"
    )

    op.execute("ALTER TABLE ledger_account DROP CONSTRAINT ledger_account_tenant_id_fkey")
    _backfill("ledger_account")
    op.execute(
        "ALTER TABLE ledger_account ADD CONSTRAINT ledger_account_tenant_id_fkey "
        "FOREIGN KEY (tenant_id) REFERENCES tenant(id)"
    )

    op.execute("ALTER TABLE md_ingest_batch DROP CONSTRAINT md_ingest_batch_tenant_id_fkey")
    _backfill("md_ingest_batch")
    op.execute(
        "ALTER TABLE md_ingest_batch ADD CONSTRAINT md_ingest_batch_tenant_id_fkey "
        "FOREIGN KEY (tenant_id) REFERENCES tenant(id)"
    )

    op.execute(
        "ALTER TABLE md_ingest_batch_tick DROP CONSTRAINT md_ingest_batch_tick_tenant_id_fkey"
    )
    _backfill("md_ingest_batch_tick")
    op.execute(
        "ALTER TABLE md_ingest_batch_tick ADD CONSTRAINT md_ingest_batch_tick_tenant_id_fkey "
        "FOREIGN KEY (tenant_id) REFERENCES tenant(id)"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE md_ingest_batch_tick DROP CONSTRAINT md_ingest_batch_tick_tenant_id_fkey"
    )
    op.execute(
        "ALTER TABLE md_ingest_batch_tick ADD CONSTRAINT md_ingest_batch_tick_tenant_id_fkey "
        "FOREIGN KEY (tenant_id) REFERENCES users(user_id)"
    )

    op.execute("ALTER TABLE md_ingest_batch DROP CONSTRAINT md_ingest_batch_tenant_id_fkey")
    op.execute(
        "ALTER TABLE md_ingest_batch ADD CONSTRAINT md_ingest_batch_tenant_id_fkey "
        "FOREIGN KEY (tenant_id) REFERENCES users(user_id)"
    )

    op.execute("ALTER TABLE ledger_account DROP CONSTRAINT ledger_account_tenant_id_fkey")
    op.execute(
        "ALTER TABLE ledger_account ADD CONSTRAINT ledger_account_tenant_id_fkey "
        "FOREIGN KEY (tenant_id) REFERENCES users(user_id)"
    )

    op.execute("ALTER TABLE pos_snapshot DROP CONSTRAINT pos_snapshot_tenant_id_fkey")
    op.execute(
        "ALTER TABLE pos_snapshot ADD CONSTRAINT pos_snapshot_tenant_id_fkey "
        "FOREIGN KEY (tenant_id) REFERENCES users(user_id)"
    )

    op.execute("ALTER TABLE pos_journal DROP CONSTRAINT pos_journal_tenant_id_fkey")
    op.execute(
        "ALTER TABLE pos_journal ADD CONSTRAINT pos_journal_tenant_id_fkey "
        "FOREIGN KEY (tenant_id) REFERENCES users(user_id)"
    )

    op.execute("ALTER TABLE pos_account DROP CONSTRAINT pos_account_tenant_id_fkey")
    op.execute(
        "ALTER TABLE pos_account ADD CONSTRAINT pos_account_tenant_id_fkey "
        "FOREIGN KEY (tenant_id) REFERENCES users(user_id)"
    )
