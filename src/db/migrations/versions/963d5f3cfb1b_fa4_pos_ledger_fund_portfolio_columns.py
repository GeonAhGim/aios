"""FA-4 — 소급 마이그레이션 B: pos_account·pos_journal·pos_snapshot·
ledger_journal_entry·ledger_posting_line에 fund_id/portfolio_id 컬럼+FK+백필
(WORM 테이블은 컬럼만).

Revision ID: 963d5f3cfb1b
Revises: 789c138f13fe
Create Date: 2026-09-06 15:00:00.000000

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-4
(§9 표·§2.1·§4). PM 2026-09-06 decision(task-1794) — FA-3과 동일한
armed-cutover(선택지 B)를 그대로 이어간다. 이 리프에서 NOT NULL을
강제하지 않는다(사유는 789c138f13fe와 동일 — jurisdiction/base_currency
등 부트스트랩 데이터가 기존 사용자 전원에게 없음).

백필 규칙: FA-1 `domain/defaults.py`의 결정론 규칙(`default_fund_id`/
`default_portfolio_id`, user_id 단일 인자의 UUIDv5)만 재사용한다 — 789c138f13fe와
같은 패턴으로, 대상 행의 tenant_id마다 그 규칙으로 fund_id/portfolio_id를
계산하고 그 id의 fund/portfolio 행이 **이미 존재하는 경우에만** 백필한다.

WORM 충돌 사전 결정(decision에 명시된 사실 확인, information_schema.triggers로
착수 전 검증함): `pos_journal`(4a1d0c0de004)·`ledger_journal_entry`·
`ledger_posting_line`(4a1d0c0de005, LC-6)은 이미 append-only WORM 트리거
(`*_worm_guard_trg`, BEFORE UPDATE OR DELETE ... RAISE EXCEPTION)가 걸려
있어 기존 행이 물리적으로 재기록 불가하다 — 789c138f13fe의 `fills`와 동일한
이유로 이 세 테이블은 컬럼/FK/인덱스만 추가하고 백필하지 않는다(영구 NULL,
신규 쓰기는 FA-5 이후 배선). 트리거를 DROP·DISABLE·
`session_replication_role`로 우회하는 방법은 I-04 위반이라 채택하지
않는다. `pos_account`·`pos_snapshot`은 트리거가 없음을 같은 방법으로
확인했다(정상적으로 갱신되는 가변 상태) — 이 둘만 백필한다.

`pos_account`/`pos_snapshot`은 `tenant_id` 컬럼이 789c138f13fe의
`orders.user_id`와 같은 역할(사용자 식별)을 한다 — 백필 조회 키로 그대로
쓴다.
"""
from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

from src.foundation.entities.domain.defaults import default_fund_id, default_portfolio_id

# revision identifiers, used by Alembic.
revision: str = "963d5f3cfb1b"
down_revision: str | Sequence[str] | None = "789c138f13fe"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BACKFILL_TABLES = ("pos_account", "pos_snapshot")
_ALL_TABLES = (
    "pos_account",
    "pos_journal",
    "pos_snapshot",
    "ledger_journal_entry",
    "ledger_posting_line",
)


def upgrade() -> None:
    for table in _ALL_TABLES:
        op.execute(f"ALTER TABLE {table} ADD COLUMN fund_id UUID REFERENCES fund(fund_id)")
        op.execute(
            f"ALTER TABLE {table} ADD COLUMN portfolio_id UUID REFERENCES portfolio(portfolio_id)"
        )
        op.execute(
            f"CREATE INDEX idx_{table}_fund_id ON {table}(fund_id) WHERE fund_id IS NOT NULL"
        )
        op.execute(
            f"CREATE INDEX idx_{table}_portfolio_id ON {table}(portfolio_id) "
            "WHERE portfolio_id IS NOT NULL"
        )

    for table in _BACKFILL_TABLES:
        _backfill_by_tenant_id(table)


def _backfill_by_tenant_id(table: str) -> None:
    bind = op.get_bind()
    tenant_ids = [
        row[0]
        for row in bind.execute(
            text(f"SELECT DISTINCT tenant_id FROM {table}")  # noqa: S608 — table은 상수 튜플에서만 옴
        ).fetchall()
    ]
    for tenant_id in tenant_ids:
        fund_id = default_fund_id(tenant_id)
        portfolio_id = default_portfolio_id(tenant_id)
        bind.execute(
            text(
                f"UPDATE {table} SET fund_id = :fund_id, portfolio_id = :portfolio_id "  # noqa: S608
                "WHERE tenant_id = :tenant_id "
                "AND EXISTS (SELECT 1 FROM fund WHERE fund_id = :fund_id) "
                "AND EXISTS ("
                "  SELECT 1 FROM portfolio "
                "  WHERE portfolio_id = :portfolio_id AND fund_id = :fund_id"
                ")"
            ),
            {"fund_id": fund_id, "portfolio_id": portfolio_id, "tenant_id": tenant_id},
        )


def downgrade() -> None:
    for table in reversed(_ALL_TABLES):
        op.execute(f"DROP INDEX IF EXISTS idx_{table}_portfolio_id")
        op.execute(f"DROP INDEX IF EXISTS idx_{table}_fund_id")
        op.execute(f"ALTER TABLE {table} DROP COLUMN portfolio_id")
        op.execute(f"ALTER TABLE {table} DROP COLUMN fund_id")
