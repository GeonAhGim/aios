"""strategy_purchases confirmed_at 컬럼 추가

Revision ID: b4c5d6e7f8a9
Revises: a3b4c5d6e7f8
Create Date: 2026-08-28 00:26:09.061936

Spec: 기능설계문서_v1.20.md#FD-18.5b

편차: FD-18.5b 출력이 {purchase_id, payment_status, confirmed_at}을
요구하지만 strategy_purchases에 결제 확인 시각을 저장할 컬럼이 없었다
(설계 누락) — 신설. 이미 CONFIRMED인 건을 재확인(멱등) 요청해도 이
컬럼 덕분에 실제 확인 시각을 정직하게 다시 반환할 수 있다.
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b4c5d6e7f8a9"
down_revision: str | None = "a3b4c5d6e7f8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE strategy_purchases ADD COLUMN confirmed_at TIMESTAMPTZ")


def downgrade() -> None:
    op.execute("ALTER TABLE strategy_purchases DROP COLUMN confirmed_at")
