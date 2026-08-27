"""approval_requests

Revision ID: 189bdd25a017
Revises: 2a4eac7061ae
Create Date: 2026-08-28 00:26:09.061936

Spec: 기능설계문서_v1.20.md#FD-10.1/10.2, ADR-2026-08-10-D

04번 문서는 이 테이블을 "착수 시 FD-10.1 승인테이블과 통합" 예정으로만
남겨뒀고 실제 DDL이 없었음 — 여기서 신규 설계. scope로 사용자 레벨(SOLO/
DUAL, 60초 하한)과 플랫폼 레벨(1인 조건부 확정, 180초 하한, ADR-2026-08-10-D)을
한 테이블로 표현한다. provenance는 FD-10.2 SURGE 배치승인의 Trigger
Provenance 태그.
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "189bdd25a017"
down_revision: str | None = "2a4eac7061ae"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE approval_requests (
            id                      BIGSERIAL PRIMARY KEY,
            scope                   VARCHAR(10) NOT NULL CHECK (scope IN ('USER','PLATFORM')),
            user_id                 UUID,  -- FK: 11.1에서 ALTER TABLE로 추가. PLATFORM이면 NULL
            trigger_source          VARCHAR(50) NOT NULL,
            provenance              VARCHAR(100),  -- FD-10.2 Trigger Provenance 태그
            context                 JSONB NOT NULL,
            requested_action        VARCHAR(100) NOT NULL,
            approval_mode           VARCHAR(10) NOT NULL CHECK (approval_mode IN ('SOLO','DUAL')),
            status                  VARCHAR(20) NOT NULL DEFAULT 'PENDING'
                CHECK (status IN ('PENDING','APPROVED','REJECTED','EXPIRED','CANCELLED')),
            mandatory_wait_seconds  INT NOT NULL,
            first_approver_id       UUID,
            second_approver_id      UUID,
            created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
            expires_at              TIMESTAMPTZ NOT NULL,
            resolved_at             TIMESTAMPTZ
        )
        """
    )
    op.execute("CREATE INDEX idx_approval_requests_status ON approval_requests(status)")
    op.execute("CREATE INDEX idx_approval_requests_user ON approval_requests(user_id)")
    op.execute(
        """
        ALTER TABLE system_safety_state ADD CONSTRAINT fk_safety_state_reactivation
            FOREIGN KEY (reactivation_approval_id) REFERENCES approval_requests(id)
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE system_safety_state DROP CONSTRAINT fk_safety_state_reactivation")
    op.execute("DROP TABLE approval_requests")
