"""notifications

Revision ID: 39ebfce4f07f
Revises: f5dd798b2e28
Create Date: 2026-08-28 00:26:09.061936

Spec: 04_db_schema_v1.7.md (Notifications, FD-17). 작업트리 3.15 —
17.1 착수 전 선행 필요(10번 문서 각주에 따라 지금 적용).

편차: user_id의 REFERENCES users(user_id)는 11.1에서 ALTER TABLE로 추가한다
(a7c02fa80d22 참조 — users 테이블이 아직 없음).
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "39ebfce4f07f"
down_revision: str | None = "f5dd798b2e28"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE notifications (
            id              BIGSERIAL PRIMARY KEY,
            user_id         UUID NOT NULL,  -- FK: 11.1에서 ALTER TABLE로 추가
            event_type      VARCHAR(50) NOT NULL,
            channel         VARCHAR(20) NOT NULL CHECK (channel IN ('EMAIL','PUSH','IN_APP')),
            status          VARCHAR(20) NOT NULL CHECK (status IN ('SENT','FAILED')),
            payload_summary JSONB,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_notifications_user ON notifications(user_id, created_at DESC)"
    )
    op.execute(
        """
        CREATE TABLE notification_preferences (
            user_id                     UUID PRIMARY KEY,  -- FK: 11.1에서 ALTER TABLE로 추가
            marketplace_purchase_email  BOOLEAN NOT NULL DEFAULT true,
            verification_result_email   BOOLEAN NOT NULL DEFAULT true,
            risk_mismatch_email         BOOLEAN NOT NULL DEFAULT true
            -- human_approval_requested/watchdog_triggered/execution_blocked 등
            -- 강제 채널 항목은 컬럼 자체를 두지 않는다(FD-17.4 서버측 거부의
            -- 이중 방어 — 04번 원문 원칙 그대로).
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE notification_preferences")
    op.execute("DROP TABLE notifications")
