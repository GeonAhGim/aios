"""device_tokens

Revision ID: c5d6e7f8a9b0
Revises: b4c5d6e7f8a9
Create Date: 2026-08-28 00:26:09.061936

Spec: 04_db_schema_v1.7.md (Device Tokens, FD-21.1). 작업트리 3.16 —
21.1 착수 전 선행 필요(10번 문서 각주에 따라 지금 적용).

활성 토큰끼리만 유니크(부분 유니크 인덱스) — 해지(is_active=false) 후
같은 토큰으로 재등록해도 INSERT 자체가 막히지 않도록(v1.3 재점검
라운드 정정, 04번 원문 그대로).
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c5d6e7f8a9b0"
down_revision: str | None = "b4c5d6e7f8a9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE device_tokens (
            id              BIGSERIAL PRIMARY KEY,
            user_id         UUID NOT NULL REFERENCES users(user_id),
            device_token    VARCHAR(255) NOT NULL,
            platform        VARCHAR(10) NOT NULL CHECK (platform IN ('iOS','Android')),
            is_active       BOOLEAN NOT NULL DEFAULT TRUE,
            registered_at   TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX idx_device_tokens_active_unique "
        "ON device_tokens(user_id, device_token) WHERE is_active"
    )
    op.execute(
        "CREATE INDEX idx_device_tokens_user ON device_tokens(user_id) WHERE is_active"
    )


def downgrade() -> None:
    op.execute("DROP TABLE device_tokens")
