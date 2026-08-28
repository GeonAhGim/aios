"""exchange_credentials

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-08-28 00:26:09.061936

Spec: 13_multi_tenancy_auth_v1.4.md#§13.3. 작업트리 12.1.

api_key/api_secret은 각자 암호화(BYTEA)로 저장, extra_encrypted는 거래소별
추가 필드(Bitget의 api_passphrase, KIS의 cano/acnt_prdt_cd 등)를 JSON으로
묶어 암호화한 것 — 거래소마다 필요한 필드 개수가 달라 컬럼을 늘리지 않고
여기 하나로 흡수한다.
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4e5f6a7b8c9"
down_revision: str | None = "c3d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE exchange_credentials (
            id                      BIGSERIAL PRIMARY KEY,
            user_id                 UUID NOT NULL REFERENCES users(user_id),
            exchange                VARCHAR(30) NOT NULL,
            api_key_encrypted       BYTEA NOT NULL,
            api_secret_encrypted    BYTEA NOT NULL,
            extra_encrypted         BYTEA,
            is_active               BOOLEAN NOT NULL DEFAULT TRUE,
            linked_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
            revoked_at              TIMESTAMPTZ,
            UNIQUE (user_id, exchange)
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_exchange_credentials_user ON exchange_credentials(user_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE exchange_credentials")
