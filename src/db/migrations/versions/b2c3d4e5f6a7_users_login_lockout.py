"""users 로그인 실패 잠금 컬럼 추가

Revision ID: b2c3d4e5f6a7
Revises: a1f2b3c4d5e6
Create Date: 2026-08-28 00:26:09.061936

Spec: 기능설계문서_v1.20.md#FD-11.1, 13_multi_tenancy_auth_v1.4.md#§13.2

편차: 13번 §13.2 users DDL에는 FD-11.1이 요구하는 "5회 연속 로그인 실패 시
15분 계정 잠금" 상태를 저장할 컬럼이 없었다(설계 누락) — 여기서 신설,
13번 문서를 v1.4로 갱신.
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b2c3d4e5f6a7"
down_revision: str | None = "a1f2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE users ADD COLUMN failed_login_attempts SMALLINT NOT NULL DEFAULT 0"
    )
    op.execute("ALTER TABLE users ADD COLUMN locked_until TIMESTAMPTZ")


def downgrade() -> None:
    op.execute("ALTER TABLE users DROP COLUMN locked_until")
    op.execute("ALTER TABLE users DROP COLUMN failed_login_attempts")
