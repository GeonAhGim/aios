"""strategy_executions.max_drawdown_pct — 자동 손절 리스크 가드 (신설)

Revision ID: 8e22a459e6ab
Revises: a1b2c3d4e5f6
Create Date: 2026-09-02 00:00:00.000000

편차(병합 중 발견): 원래 revision id가 `b2c3d4e5f6a7`였는데, 다른 세션이
만든 `b2c3d4e5f6a7_users_login_lockout.py`와 완전히 같은 ID를 우연히
재사용해 `alembic heads`가 "Revision b2c3d4e5f6a7 is present more than
once" 경고를 냈다(둘 중 하나만 로드되는 미정의 동작 — 실제로 이 파일이
가려져 있었다). 이 파일 쪽을 새 ID로 바꿔 충돌을 해소한다 —
`c3d4e5f6a7b8_withdrawal_whitelist.py`의 `Revises: b2c3d4e5f6a7`는 파일
본문(users FK를 전제)상 users_login_lockout 쪽을 가리키는 게 맞으므로
그대로 둔다.

Spec: 사용자 요청(2026-09-01) — ZuluTrade식 "위험 관리"(ZuluGuard, 손실
한도 도달 시 자동 정지) 기능. execution_service.py::pause()가 이미
paused_by='SAFETY_LAYER'를 1급 값으로 지원하도록 설계돼 있었고(사람이
아닌 안전장치가 실행을 멈추는 경로), FD-17 NotificationGateway도
"execution.safety_block.applied" 이벤트를 이미 구독 목록에 등록해뒀지만
(gateway.py) 실제로 그 이벤트를 발행하는 곳이 어디에도 없었다 — 이
기능이 그 첫 실제 발행자가 된다.

max_drawdown_pct가 NULL이면 리스크 가드 비활성(기본값) — 사용자가
명시적으로 설정해야만 자동 정지가 켜진다.
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8e22a459e6ab"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE strategy_executions ADD COLUMN max_drawdown_pct NUMERIC(5,2) "
        "CHECK (max_drawdown_pct > 0 AND max_drawdown_pct <= 100)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE strategy_executions DROP COLUMN max_drawdown_pct")
