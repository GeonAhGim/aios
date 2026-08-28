"""disputes

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-08-28 00:26:09.061936

Spec: 04_db_schema_v1.7.md (Disputes, FD-13.10), 14번 문서 §14.5. 작업트리
3.18 — 13.10 착수 전 선행 필요(10번 문서 각주에 따라 지금 적용).

resolution_decision/resolution_reason/resolved_by/resolved_at은 FD-18.2
(운영자 분쟁 처리, 아직 없음) 소관 — 지금은 컬럼만 마련해두고 이 leaf
(13.10, 분쟁 접수)에서는 INSERT하지 않는다.
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c9d0e1f2a3b4"
down_revision: str | None = "b8c9d0e1f2a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE disputes (
            id                  BIGSERIAL PRIMARY KEY,
            purchase_id         BIGINT NOT NULL REFERENCES strategy_purchases(id),
            submitted_by        UUID NOT NULL REFERENCES users(user_id),
            reason              TEXT NOT NULL,
            status              VARCHAR(20) NOT NULL DEFAULT 'OPEN'
                CHECK (status IN ('OPEN', 'RESOLVED')),
            resolution_decision VARCHAR(30)
                CHECK (resolution_decision IN ('NORMAL_RISK_REALIZATION', 'DELISTED_AND_REFUND')),
            resolution_reason   TEXT,
            resolved_by         UUID REFERENCES users(user_id),
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            resolved_at         TIMESTAMPTZ
        )
        """
    )
    op.execute("CREATE INDEX idx_disputes_status ON disputes(status)")
    op.execute(
        "CREATE UNIQUE INDEX idx_disputes_open_per_purchase "
        "ON disputes(purchase_id) WHERE status = 'OPEN'"
    )


def downgrade() -> None:
    op.execute("DROP TABLE disputes")
