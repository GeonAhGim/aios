"""strategy_listings 검색/정렬용 컬럼 추가 (verified_at, sharpe_ratio)

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-08-28 00:26:09.061936

Spec: 기능설계문서_v1.20.md#FD-13.8, 14번 문서 §14.4

편차: FD-13.8이 요구하는 기본 정렬 기준(검증통과일 역순, 2차 샤프비율
내림차순)을 저장할 컬럼이 strategy_listings 어디에도 없었다(설계 누락).
- verified_at: VerificationService.decide()의 APPROVE 경로에서 기록.
- sharpe_ratio: 실제 백테스트 엔진이 아직 없어(설계 스콥 밖) 채워주는
  곳이 없는 Draft 컬럼 — NULL은 정렬 쿼리에서 항상 마지막으로 밀린다.
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7b8c9d0e1f2"
down_revision: str | None = "f6a7b8c9d0e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE strategy_listings ADD COLUMN verified_at TIMESTAMPTZ")
    op.execute("ALTER TABLE strategy_listings ADD COLUMN sharpe_ratio NUMERIC(10,4)")


def downgrade() -> None:
    op.execute("ALTER TABLE strategy_listings DROP COLUMN sharpe_ratio")
    op.execute("ALTER TABLE strategy_listings DROP COLUMN verified_at")
