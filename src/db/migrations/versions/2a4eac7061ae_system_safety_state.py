"""system_safety_state

Revision ID: 2a4eac7061ae
Revises: 39ebfce4f07f
Create Date: 2026-08-28 00:26:09.061936

Spec: 04_db_schema_v1.7.md (System Safety State, FD-9.4/9.4b)

단일 행만 허용 — 여러 FastAPI 워커 프로세스가 시스템 전역 Circuit Breaker
레벨을 공유해야 하므로 in-memory 변수가 아니라 DB에 영속화한다.
reactivation_approval_id의 FK는 approval_requests 테이블 생성 후(다음
마이그레이션) ALTER TABLE로 추가한다(순환 없음 — 단순히 순서 문제).
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2a4eac7061ae"
down_revision: str | None = "39ebfce4f07f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE system_safety_state (
            id                  SMALLINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
            circuit_breaker_level VARCHAR(20) NOT NULL DEFAULT 'normal'
                CHECK (circuit_breaker_level IN
                    ('normal','warning','restricted','halted','emergency')),
            reactivation_approval_id BIGINT,
            updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("INSERT INTO system_safety_state (id) VALUES (1) ON CONFLICT DO NOTHING")


def downgrade() -> None:
    op.execute("DROP TABLE system_safety_state")
