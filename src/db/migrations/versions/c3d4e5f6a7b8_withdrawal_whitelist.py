"""withdrawal_whitelist

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-08-28 00:26:09.061936

Spec: 04_db_schema_v1.7.md (withdrawal_whitelist, FD-11.5). 작업트리 3.19 —
11.7 착수 전 선행 필요(10번 문서 각주에 따라 지금 적용).

users 테이블이 이미 있어 FK를 처음부터 바로 연결한다(11.1 때처럼 나중에
ALTER TABLE로 미룰 필요 없음). 삭제(revoke)는 의도적으로 미제공 —
화이트리스트에서 빼는 것도 위기 상황 중에는 공격 표면이 될 수 있어
착수 시 "평상시만 삭제 가능" 원칙 검토가 필요하다는 04번 원문 그대로.
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3d4e5f6a7b8"
down_revision: str | None = "b2c3d4e5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE withdrawal_whitelist (
            id                  BIGSERIAL PRIMARY KEY,
            user_id             UUID NOT NULL REFERENCES users(user_id),
            exchange            VARCHAR(30) NOT NULL,
            destination_address TEXT NOT NULL,
            label               VARCHAR(100),
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX idx_withdrawal_whitelist_user ON withdrawal_whitelist(user_id)")


def downgrade() -> None:
    op.execute("DROP TABLE withdrawal_whitelist")
