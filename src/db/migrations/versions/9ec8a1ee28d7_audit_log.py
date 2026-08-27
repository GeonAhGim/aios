"""audit_log

Revision ID: 9ec8a1ee28d7
Revises: 0ff10faffd25
Create Date: 2026-08-28 00:26:09.061936

Spec: 04_db_schema_v1.6.md (Audit Log, 8.10 — Append-only, WORM)

편차: user_id의 REFERENCES users(user_id)는 11.1에서 ALTER TABLE로 추가한다
(a7c02fa80d22 참조).

주의: REVOKE ... FROM PUBLIC은 테이블 소유자(이 마이그레이션을 실행하는 DB
역할)에게는 적용되지 않는다(PostgreSQL 원칙 — 소유자는 GRANT/REVOKE와
무관하게 항상 전체 권한 보유). 실제 WORM 강제를 위해서는 애플리케이션이
테이블 소유자가 아닌 별도 역할로 접속해야 한다 — 이 역할 분리는 아직 이
프로젝트에 존재하지 않아 인프라 셋업 단계에서 별도로 다뤄야 한다.
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9ec8a1ee28d7"
down_revision: str | Sequence[str] | None = "0ff10faffd25"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE audit_log (
            log_id          BIGSERIAL PRIMARY KEY,
            user_id         UUID,  -- FK: 11.1에서 ALTER TABLE로 추가. NULL 허용 — 시스템 레벨 행위
            actor_agent     VARCHAR(100) NOT NULL,
            action_type     VARCHAR(50) NOT NULL,
            target_type     VARCHAR(50),
            target_id       VARCHAR(100),
            decision_data   JSONB NOT NULL,
            verification_chain JSONB,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    # WORM(Write-Once-Read-Many) 강제 — 16.3 원칙
    op.execute("REVOKE UPDATE, DELETE ON audit_log FROM PUBLIC")
    op.execute("CREATE INDEX idx_audit_actor ON audit_log(actor_agent)")
    op.execute("CREATE INDEX idx_audit_target ON audit_log(target_type, target_id)")
    op.execute("CREATE INDEX idx_audit_created ON audit_log(created_at)")


def downgrade() -> None:
    op.execute("DROP TABLE audit_log")
