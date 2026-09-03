"""exchange_credentials_key_version_scope

Revision ID: 5a0aedee0af0
Revises: a9445f6ca04c
Create Date: 2026-09-04 00:00:00.000000

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md#§9 PLT-33
(+ §10-8). `scope`(PAPER/LIVE)를 UNIQUE에 편입해 같은 (user_id, exchange)
조합이 스코프별로 각각 자격증명을 가질 수 있게 한다 — 프론트가 아직
scope를 보내지 않으므로 기존 행은 전부 `scope='PAPER'`로 백필한다
(ADR-2026-08-29-E, LIVE 경로는 이 리프에서 열지 않는다). `key_version`은
그 행을 암호화한 KeyRing kid(§9 PLT-31) 스냅샷 — 기존 행은 `legacy_encrypt`
로 생성된 접두 없는 토큰이므로 kid="legacy"로 백필한다(PLT-34 회전 스크립트가
복호 없이 회전 대상을 찾는 인덱스로 재사용).
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5a0aedee0af0"
down_revision: str | None = "a9445f6ca04c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE exchange_credentials
            ADD COLUMN scope VARCHAR(10) NOT NULL DEFAULT 'PAPER'
                CHECK (scope IN ('PAPER', 'LIVE')),
            ADD COLUMN key_version VARCHAR(64) NOT NULL DEFAULT 'legacy'
        """
    )
    op.execute(
        "ALTER TABLE exchange_credentials "
        "DROP CONSTRAINT exchange_credentials_user_id_exchange_key"
    )
    op.execute(
        "ALTER TABLE exchange_credentials "
        "ADD CONSTRAINT exchange_credentials_user_id_exchange_scope_key "
        "UNIQUE (user_id, exchange, scope)"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE exchange_credentials "
        "DROP CONSTRAINT exchange_credentials_user_id_exchange_scope_key"
    )
    op.execute(
        "ALTER TABLE exchange_credentials "
        "ADD CONSTRAINT exchange_credentials_user_id_exchange_key "
        "UNIQUE (user_id, exchange)"
    )
    op.execute(
        "ALTER TABLE exchange_credentials DROP COLUMN scope, DROP COLUMN key_version"
    )
