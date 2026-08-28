"""users seller_suspended 컬럼 추가

Revision ID: a3b4c5d6e7f8
Revises: f2a3b4c5d6e7
Create Date: 2026-08-28 00:26:09.061936

Spec: 13_multi_tenancy_auth_v1.4.md#§13.5, 14번 문서 §14.5.3

13.1에서 명시적으로 미뤄뒀던 컬럼 — FD-18.4(판매자 정지)가 실제로
필요해진 지금 착수한다.
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a3b4c5d6e7f8"
down_revision: str | None = "f2a3b4c5d6e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE users ADD COLUMN seller_suspended BOOLEAN NOT NULL DEFAULT FALSE")


def downgrade() -> None:
    op.execute("ALTER TABLE users DROP COLUMN seller_suspended")
