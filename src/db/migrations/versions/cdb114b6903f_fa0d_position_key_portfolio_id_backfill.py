"""FA-0d -- position_key portfolio_id incorporation: pos_snapshot backfill.

Revision ID: cdb114b6903f
Revises: 18965d657219
Create Date: 2026-09-09 05:00:00.000000

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-0d
(§9 table row 113). task-1943.

`domain/position_key.py` (`PositionKey`) changed from 4 parts
(`venue:instrument_id:strategy_id:execution_id`) to 5 parts (+`portfolio_id`)
(task-1943) -- since the multi-portfolio transition, the same venue/instrument/
strategy/execution combination can now be open concurrently in different
portfolios, so 4 parts alone can no longer uniquely identify a position.
`record_fill`/`rebuild_snapshot`/`record_funding_fee` now enforce the 5-part
format via `PositionKey.parse()`, so the `position_key` (PK) of existing
`pos_snapshot` rows must also be rewritten to the new format for those rows
to keep being readable.

The `portfolio_id` value is not recomputed -- FA-4 (`963d5f3cfb1b`) has
already backfilled the `pos_snapshot.portfolio_id` column to the per-tenant_id
FA-1 default portfolio, so that column value is reused as-is (even if an
admin later actually reassigned it to a different portfolio, this column is
the SSOT).

Fail-closed handling for unbackfillable rows (PM decision, task-1943): FA-4
silently skipped rows where the backfill failed (tenants without a
bootstrapped portfolio), but this migration changes `position_key` itself
(the PK), so silently skipping such a row would leave it an inaccessible
zombie row that can never pass the `PositionKey.parse()` check enforcing the
new 5-part format. So if even one row has `portfolio_id` NULL or an existing
`position_key` that is not exactly 4 parts, the migration fails right there
on the spot (the operator must first finish the FA-4 backfill / portfolio
bootstrap). Rows already in the 5-part format (whose final component is a
valid UUID) are skipped as-is (idempotent on rerun).

WORM: `pos_journal` is left untouched (same reason as `963d5f3cfb1b` --
append-only triggers physically block UPDATE/DELETE; bypassing that would
violate I-04 and is not allowed). As a result, for positions that already had
journal entries at the time of this migration, querying `journal.list_for()`
with the new `position_key` after the backfill will not show those prior
entries (they remain queryable only under the old key) -- this is the same
class of WORM technical debt as FA-4 leaving `pos_journal.fund_id`/
`portfolio_id` permanently NULL.

Real-DB (TEST_DATABASE_URL) verification result (task-1943 note): at the time
this work started, the local test DB had 0 rows in both `pos_snapshot` and
`pos_journal`, so this backfill path itself was never actually exercised --
`pos_journal.UNIQUE(position_key, sequence_no)` is structurally unaffected
because this migration never touches `pos_journal` at all (only new writes
use the new 5-part key, and existing rows keep the old key as-is -- there is
no practical case where the two formats collide on the same string).
`tests/foundation/integration/
positions/test_migration_fa0d_position_key_portfolio_id.py` verifies both the
backfill-success and backfill-failure (NULL portfolio_id) branches against
the real DB using synthetic rows.

`pos_snapshot` has `no_update_guard` (`a2c4f9e1b3d5`, FA-10) attached, which
blocks changing the PK `position_key` via UPDATE -- this is replaced with the
same DELETE(old) + INSERT(new, other columns identical) pattern that
`position_ledger.py` uses when updating `legacy_position_id`.
"""
from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision: str = "cdb114b6903f"
down_revision: str | Sequence[str] | None = "18965d657219"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS = (
    "position_key", "tenant_id", "account_id", "instrument_id", "quantity", "avg_cost",
    "cost_method", "lots", "realized_pnl_base", "unrealized_pnl_base", "fees_base",
    "funding_base", "mark_price", "mark_at", "last_journal_seq", "legacy_position_id",
    "fund_id", "portfolio_id", "valid_from", "valid_to", "tx_from", "tx_to", "updated_at",
)
_OTHER_COLUMNS = ", ".join(c for c in _COLUMNS if c != "position_key")


class UnbackfillablePositionKeyError(RuntimeError):
    """Raised when an unbackfillable `pos_snapshot` row halts the migration on the spot."""


_REPLACE_SQL = (
    "WITH prior AS ("  # noqa: S608 -- col names from _COLUMNS const tuple only; vals are bound
    "  DELETE FROM pos_snapshot WHERE position_key = :old_key RETURNING *"
    ") "
    f"INSERT INTO pos_snapshot (position_key, {_OTHER_COLUMNS}) "
    f"SELECT :new_key, {_OTHER_COLUMNS} FROM prior"
)


def _replace_position_key(old_key: str, new_key: str) -> None:
    bind = op.get_bind()
    bind.execute(text(_REPLACE_SQL), {"old_key": old_key, "new_key": new_key})
    # 위 DELETE+INSERT는 DEFERRABLE인 pos_snapshot_legacy_position_id_fkey
    # (963d5f3cfb1b) 재검사를 트랜잭션 끝까지 미룬다. alembic은 upgrade/downgrade
    # 전체를 한 트랜잭션으로 묶으므로(env.py), 이 마이그레이션 다음에 실행되는
    # 다른 리비전이 같은 트랜잭션 안에서 pos_snapshot에 ALTER TABLE을 걸면(예:
    # a2c4f9e1b3d5 downgrade) 대기 중인 트리거 이벤트 때문에 Postgres가
    # ObjectInUseError로 거부한다 — 여기서 바로 확정지어 다음 리비전으로
    # 미결 상태를 넘기지 않는다.
    bind.execute(text("SET CONSTRAINTS pos_snapshot_legacy_position_id_fkey IMMEDIATE"))


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(text("SELECT position_key, portfolio_id FROM pos_snapshot")).fetchall()

    for position_key, portfolio_id in rows:
        parts = position_key.split(":")
        if len(parts) == 5:
            continue  # already in new format (idempotent on rerun)
        if len(parts) != 4:
            raise UnbackfillablePositionKeyError(
                f"pos_snapshot.position_key={position_key!r}: 4부분 레거시 형식이 "
                "아니라 자동 백필할 수 없습니다(역산 불가)."
            )
        if portfolio_id is None:
            raise UnbackfillablePositionKeyError(
                f"pos_snapshot.position_key={position_key!r}: portfolio_id가 NULL입니다"
                "(FA-4 백필 미완료) -- 먼저 해당 tenant의 기본 포트폴리오를 "
                "부트스트랩하세요(역산 불가)."
            )
        new_key = ":".join((*parts, str(portfolio_id)))
        _replace_position_key(position_key, new_key)


def downgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(text("SELECT position_key FROM pos_snapshot")).fetchall()

    for (position_key,) in rows:
        parts = position_key.split(":")
        if len(parts) != 5:
            continue  # already in old format
        old_key = ":".join(parts[:4])
        _replace_position_key(position_key, old_key)
