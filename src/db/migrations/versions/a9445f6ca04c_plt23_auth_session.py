"""PLT-23 M3 — auth_session(refresh 회전·세션 revoke 대상 테이블).

Revision ID: a9445f6ca04c
Revises: f4a6b8c0d2e4

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md §2 M3 DDL, §9 PLT-23.

`refresh_hash`는 sha256 hex(64자) — 평문 refresh 토큰은 응답에만 노출되고
DB에는 절대 저장하지 않는다(§3.4). `revoked_at IS NULL` 부분 인덱스는
"활성 세션 조회"가 이 테이블의 가장 빈번한 접근 패턴이라(§3.4 매 요청
JOIN) 풀 스캔을 피하기 위함이다.
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a9445f6ca04c"
down_revision: str | Sequence[str] | None = "f4a6b8c0d2e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE auth_session (
            id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id       UUID NOT NULL REFERENCES users(user_id),
            tenant_id     UUID NOT NULL,
            refresh_hash  CHAR(64) NOT NULL UNIQUE,
            auth_level    VARCHAR(16) NOT NULL DEFAULT 'PASSWORD'
                CHECK (auth_level IN ('PASSWORD','MFA_VERIFIED')),
            ip_hash       CHAR(64),
            ua_hash       CHAR(64),
            issued_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            rotated_at    TIMESTAMPTZ,
            expires_at    TIMESTAMPTZ NOT NULL,
            revoked_at    TIMESTAMPTZ,
            revoke_reason VARCHAR(40)
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_auth_session_user_active "
        "ON auth_session(user_id) WHERE revoked_at IS NULL"
    )


def downgrade() -> None:
    op.execute("DROP TABLE auth_session")
