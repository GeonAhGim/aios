"""users risk_profile 컬럼 + risk_profile_history 테이블

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
Create Date: 2026-08-28 00:26:09.061936

Spec: 13_multi_tenancy_auth_v1.4.md#§13.2, 04_db_schema_v1.7.md (FD-15.2)

10번 문서가 11.1 당시 명시적으로 미뤄뒀던 리프(3.11/3.12, "11.1 이후") —
FD-15가 실제로 필요해진 지금 착수한다.
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d0e1f2a3b4c5"
down_revision: str | None = "c9d0e1f2a3b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE users ADD COLUMN risk_profile VARCHAR(20) "
        "CHECK (risk_profile IN ('안정형','중립형','공격형'))"
    )
    op.execute("ALTER TABLE users ADD COLUMN risk_profile_assessed_at TIMESTAMPTZ")
    op.execute(
        """
        CREATE TABLE risk_profile_history (
            id                  BIGSERIAL PRIMARY KEY,
            user_id             UUID NOT NULL REFERENCES users(user_id),
            risk_profile        VARCHAR(20) NOT NULL,
            assessment_answers  JSONB NOT NULL,
            assessed_at         TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX idx_risk_profile_history_user ON risk_profile_history(user_id)")


def downgrade() -> None:
    op.execute("DROP TABLE risk_profile_history")
    op.execute("ALTER TABLE users DROP COLUMN risk_profile_assessed_at")
    op.execute("ALTER TABLE users DROP COLUMN risk_profile")
