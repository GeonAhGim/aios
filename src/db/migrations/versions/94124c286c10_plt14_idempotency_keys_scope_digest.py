"""PLT-14 — idempotency_keys에 tenant_id·request_digest·expires_at 추가.

Revision ID: 94124c286c10
Revises: be98ecef7ab7

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md §2.5 M2
`idempotency_keys_scope_digest`.

I8 불변조건("같은 Idempotency-Key + 다른 digest는 409")을 DB에도 남긴다 —
`request_digest`는 CHAR(64)(sha256 hex), NULL 허용(PLT-14 이전에 생성된
행 및 digest 대조가 필요 없는 기존 호출부는 여전히 NULL로 남긴다).
`expires_at` 기본값 24시간은 §2.3(C) 표의 `purge_expired(pool)` 배치가
지울 대상을 고르는 기준이다.
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "94124c286c10"
down_revision: str | Sequence[str] | None = "be98ecef7ab7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE idempotency_keys
            ADD COLUMN tenant_id UUID,
            ADD COLUMN request_digest CHAR(64),
            ADD COLUMN expires_at TIMESTAMPTZ NOT NULL DEFAULT now() + interval '24 hours'
        """
    )
    op.execute("CREATE INDEX idx_idem_expires ON idempotency_keys(expires_at)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_idem_expires")
    op.execute(
        """
        ALTER TABLE idempotency_keys
            DROP COLUMN tenant_id,
            DROP COLUMN request_digest,
            DROP COLUMN expires_at
        """
    )
