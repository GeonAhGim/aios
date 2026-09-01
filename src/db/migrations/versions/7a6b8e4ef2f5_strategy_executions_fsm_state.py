"""strategy_executions_fsm_state

Revision ID: 7a6b8e4ef2f5
Revises: f8a9b0c1d2e3
Create Date: 2026-09-01 00:00:00.000000

Spec: 기능설계문서_v1.21.md#FD-8.0 (ADR-2026-08-29-E)

FD-16의 strategy_executions.status(PENDING_APPROVAL/RUNNING/PAUSED/RETIRED)는
"사용자가 이 실행을 켰는가 껐는가"만 추적한다. FSM 6상태(9.11: IDLE/
BUY_ORDER_PENDING/HOLDING/SELL_ORDER_PENDING/STOP_LOSS/EMERGENCY_EXIT) 중
지금 어디에 있는지는 별도 컬럼이 필요하다 — FD-16 구현 시점엔 FD-8이
FROZEN이라 이 필요성이 드러나지 않았다.
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7a6b8e4ef2f5"
down_revision: str | Sequence[str] | None = "f8a9b0c1d2e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FSM_STATES = (
    "IDLE",
    "BUY_ORDER_PENDING",
    "HOLDING",
    "SELL_ORDER_PENDING",
    "STOP_LOSS",
    "EMERGENCY_EXIT",
)


def upgrade() -> None:
    allowed_states = ", ".join(f"'{state}'" for state in _FSM_STATES)
    op.execute(
        "ALTER TABLE strategy_executions "
        "ADD COLUMN fsm_state VARCHAR(20) NOT NULL DEFAULT 'IDLE' "
        f"CHECK (fsm_state IN ({allowed_states}))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE strategy_executions DROP COLUMN fsm_state")
