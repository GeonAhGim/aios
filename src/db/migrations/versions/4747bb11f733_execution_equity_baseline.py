"""strategy_executions equity baseline columns

Revision ID: 4747bb11f733
Revises: cdd905e63ffe
Create Date: 2026-09-03 00:00:00.000000

Spec: PM 배정 ③(agent-platform-12, 2026-09-02) — 일손실/MDD 기준점
영속화. ExecutionEquityTracker(src/services/execution_loop/equity_tracker.py)
가 이전엔 프로세스 메모리에만 갖고 있던 두 값(오늘 시작 시점 equity,
실행 시작 이후 all-time peak equity)을 재시작 후에도 복구할 수 있도록
strategy_executions에 저장한다. 전부 nullable — 기존 실행 행은 NULL
(ExecutionEquityTracker.seed()가 NULL을 "아직 기준점 없음"으로 정확히
처리하도록 이미 구현돼 있다).
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4747bb11f733"
down_revision: str | Sequence[str] | None = "cdd905e63ffe"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE strategy_executions
            ADD COLUMN equity_day_start_date DATE,
            ADD COLUMN equity_day_start_value NUMERIC(30,10),
            ADD COLUMN equity_peak_value NUMERIC(30,10)
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE strategy_executions
            DROP COLUMN equity_day_start_date,
            DROP COLUMN equity_day_start_value,
            DROP COLUMN equity_peak_value
        """
    )
