"""reconciliation_events

Revision ID: 0ff10faffd25
Revises: c8ead41fd624
Create Date: 2026-08-28 00:26:09.061936

Spec: 04_db_schema_v1.6.md (Reconciliation Log, 8.4)

편차: user_id의 REFERENCES users(user_id)는 11.1에서 ALTER TABLE로 추가한다
(a7c02fa80d22 참조).
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0ff10faffd25"
down_revision: str | Sequence[str] | None = "c8ead41fd624"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE reconciliation_events (
            id              BIGSERIAL PRIMARY KEY,
            user_id         UUID NOT NULL,  -- FK: 11.1에서 ALTER TABLE로 추가
            symbol          VARCHAR(30) NOT NULL,
            exchange        VARCHAR(30) NOT NULL,
            order_id        UUID REFERENCES orders(order_id),
            position_id     BIGINT REFERENCES positions(id),
            internal_value  JSONB NOT NULL,
            external_value  JSONB NOT NULL,
            discrepancy_pct NUMERIC(10,4),
            resolved        BOOLEAN NOT NULL DEFAULT FALSE,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    # 8.4 에스컬레이션 기준(1시간 내 3회, 24시간 내 5회) 계산용 인덱스
    op.execute(
        "CREATE INDEX idx_reconciliation_symbol_time "
        "ON reconciliation_events(symbol, exchange, created_at)"
    )
    op.execute("CREATE INDEX idx_reconciliation_order ON reconciliation_events(order_id)")


def downgrade() -> None:
    op.execute("DROP TABLE reconciliation_events")
