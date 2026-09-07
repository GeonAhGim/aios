"""FA-10 -- 소급 마이그레이션 C: 투영 테이블 양시간축 + UPDATE 금지.

Revision ID: a2c4f9e1b3d5
Revises: a1f3c9d2e5b7
Create Date: 2026-09-07 06:00:00.000000

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-10
(SS2.3, SS3, SS4 FA-A2, SS9 FA-10). FA-9(`core/bitemporal.py`,
task-1702)·FA-4(`963d5f3cfb1b`) 선행 완료.

범위(SS2.3 2026-09-06 감사 축소): 이미 물리적으로 UPDATE 불가능한(WORM
트리거) 테이블(`pos_journal`·`ledger_journal_entry`·`ledger_posting_line`·
`order_events`·`fills`·`risk_decision`)은 손대지 않는다 -- tx_from/tx_to를
추가해봐야 죽은 컬럼이다(SS2.3). 실제로 in-place로 갱신되는 투영 테이블
3개만 대상: `pos_snapshot`·`ledger_balance`·레거시 `positions`.

`md_symbol_alias`의 기존 SCD-2(`4a1d0c0de007`, valid_from/valid_to +
EXCLUDE, UPDATE로 구간을 닫음)는 그대로 둔다(task-2051 decision) -- 이
리프 범위 밖이고, 이미 적재된 시장데이터 참조 테이블의 구간 폐쇄 방식을
바꾸는 위험이 이득보다 크다는 PM 판단이다.

설계: `valid_from`/`tx_from`은 now()로 같이 채운다(이 리프는 아직 "언제
사실이 됐나"와 "언제 알았나"를 구분하지 않는다 -- 그 구분은 FA-11/12의
정정·재기표 도메인이 생긴 뒤의 일이다). `valid_to`/`tx_to`는 이 리프에서
항상 NULL로 남는다 -- 쓰기 경로는 UPDATE 대신 같은 트랜잭션 안에서 DELETE
(이전 행) + INSERT(같은 자연키의 대체 행)로 "새 버전 append"를 구현한다
(FA-A2"상태 테이블은 UPDATE 금지, 정정은 새 행" -- 문자 그대로 UPDATE
문을 실행하지 않으면서). `_current` 뷰는 DoD(3)가 요구하는 `tx_to IS
NULL` 필터를 노출한다 -- 지금은 모든 행의 tx_to가 NULL이라 항등 통과지만,
FA-11/12가 실제 정정을 쌓기 시작하면 이 필터가 "현재" 판정 기준이 된다.

`positions(id)`를 참조하는 두 FK(`pos_snapshot.legacy_position_id`,
`reconciliation_events.position_id`)는 DEFERRABLE INITIALLY DEFERRED로
바꾼다 -- `positions`의 대체 쓰기 패턴(id=X를 DELETE한 뒤 같은 id=X로
INSERT, 한 트랜잭션 안)이 DELETE 시점에 FK 위반으로 걸리지 않게 하려면
필요하다. Postgres는 DEFERRED 제약을 COMMIT 시점에만 재검사하므로, 그
시점엔 이미 같은 id의 행이 다시 존재한다.
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

# (table, constraint_name) -- information_schema로 확인한 실제 자동생성 이름.
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
