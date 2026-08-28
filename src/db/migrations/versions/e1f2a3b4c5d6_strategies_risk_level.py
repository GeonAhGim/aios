"""strategies risk_level 컬럼 추가

Revision ID: e1f2a3b4c5d6
Revises: d0e1f2a3b4c5
Create Date: 2026-08-28 00:26:09.061936

Spec: 기능설계문서_v1.20.md#FD-15.3, 9.4(MDD/VaR 등)

편차: FD-15.3이 대조 대상으로 요구하는 "전략의 리스크 지표(9.4 MDD/VaR
등 백테스트 결과)"를 저장할 컬럼이 strategies 어디에도 없었다 — 실제
백테스트 엔진이 아직 없어(9.3 정식 백테스트 스콥 밖) 그 지표들을 직접
계산해 채울 수는 없지만, FD-15.3의 3단계 등급 비교 자체는 지금
구현·테스트 가능하다. 13.8의 sharpe_ratio와 동일 원칙 — nullable Draft
컬럼으로 신설하고, 백테스트 엔진이 생기면 그때 채운다.
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e1f2a3b4c5d6"
down_revision: str | None = "d0e1f2a3b4c5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE strategies ADD COLUMN risk_level VARCHAR(20) "
        "CHECK (risk_level IN ('안정형','중립형','공격형'))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE strategies DROP COLUMN risk_level")
