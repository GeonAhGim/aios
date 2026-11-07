"""strategy_executions 단계적 노출 열 (J8 LP-4).

모의 -> 실전 전환 실행은 목표 자본(target_capital)의 일부(초기 노출 %)로 시작하고,
확대는 사용자의 별도 승인(expand-exposure)으로만 한다.

- allocated_capital: 지금 허용된 자본(포트폴리오 엔진의 포지션 크기 한도).
- target_capital: 사용자가 승인한 최종 목표.
- max_loss_amount: 사용자가 정한 손실 한도 금액. 기존 실행별 손실 가드(max_drawdown_pct)로
  환산해 강제한다.

세 열은 NULL 허용이다 -- 단계 없이 만들어진 기존 실행(모의 포함)은 그대로이고, 값이 있으면
불변식을 DB가 지킨다: 단계 자본은 목표를 넘지 못한다.

Revision ID: b7c1e5d93a40
Revises: a4f27d91c6e8
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7c1e5d93a40"
down_revision: str | None = "a4f27d91c6e8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE strategy_executions "
        "ADD COLUMN target_capital NUMERIC(30,10) CHECK (target_capital > 0), "
        "ADD COLUMN max_loss_amount NUMERIC(30,10) CHECK (max_loss_amount > 0), "
        "ADD CONSTRAINT ck_strategy_executions_stage_within_target "
        "CHECK (target_capital IS NULL OR allocated_capital <= target_capital)"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE strategy_executions "
        "DROP CONSTRAINT ck_strategy_executions_stage_within_target, "
        "DROP COLUMN max_loss_amount, "
        "DROP COLUMN target_capital"
    )
