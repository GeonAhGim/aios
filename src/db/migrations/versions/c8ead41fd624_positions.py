"""positions

Revision ID: c8ead41fd624
Revises: 210cc26533c7
Create Date: 2026-08-28 00:26:09.061936

Spec: 04_db_schema_v1.6.md (Positions)

편차: user_id의 REFERENCES users(user_id)는 11.1에서 ALTER TABLE로 추가한다
(a7c02fa80d22 참조). execution_id 컬럼은 3.14에서 ALTER TABLE로 추가한다
(orders와 동일 이유 — 210cc26533c7 참조).
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c8ead41fd624"
down_revision: str | Sequence[str] | None = "210cc26533c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE positions (
            id                   BIGSERIAL PRIMARY KEY,
            user_id              UUID NOT NULL,  -- FK: 11.1에서 ALTER TABLE로 추가
            symbol               VARCHAR(30) NOT NULL,
            exchange             VARCHAR(30) NOT NULL,
            strategy_id          VARCHAR(100) NOT NULL,
            quantity             NUMERIC(30,10) NOT NULL,
            average_entry_price  NUMERIC(30,10) NOT NULL,
            unrealized_pnl       NUMERIC(30,10) NOT NULL DEFAULT 0,
            realized_pnl         NUMERIC(30,10) NOT NULL DEFAULT 0,
            leverage             NUMERIC(10,2) NOT NULL DEFAULT 1,
            margin               NUMERIC(30,10),
            entry_time           TIMESTAMPTZ NOT NULL,
            closed_at            TIMESTAMPTZ,
            updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (symbol, exchange, strategy_id, entry_time)
        )
        """
    )
    # 09번 §9.1 #9 원칙: 포지션 청산(quantity=0) 시 행을 삭제하지 않는다.
    # closed_at을 기록하고 유지 — 현재 보유 포지션 조회는 애플리케이션 레벨에서
    # quantity<>0 AND closed_at IS NULL로 필터링한다.


def downgrade() -> None:
    op.execute("DROP TABLE positions")
