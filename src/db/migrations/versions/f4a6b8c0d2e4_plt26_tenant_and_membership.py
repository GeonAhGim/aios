"""PLT-26 M4 — tenant, tenant_membership + personal tenant backfill.

Revision ID: f4a6b8c0d2e4
Revises: 94124c286c10

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md §2 DDL, §9 PLT-26.

기존 foundation 행(consent 등)의 `tenant_id == subject_id == user_id` 불변조건을
깨지 않기 위해, 모든 기존 사용자에 대해 `id = user_id`인 PERSONAL tenant와
OWNER membership을 함께 만든다(§3.5 "PERSONAL tenant에 한해 유지"). 신규
household/organization tenant 생성은 이 리프의 범위 밖(PLT-27~29).
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f4a6b8c0d2e4"
down_revision: str | Sequence[str] | None = "94124c286c10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE tenant (
            id           UUID PRIMARY KEY,
            kind         VARCHAR(12) NOT NULL
                CHECK (kind IN ('PERSONAL','HOUSEHOLD','ORGANIZATION')),
            display_name VARCHAR(100),
            state        VARCHAR(10) NOT NULL DEFAULT 'ACTIVE'
                CHECK (state IN ('ACTIVE','SUSPENDED','DELETED')),
            created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE tenant_membership (
            id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id  UUID NOT NULL REFERENCES tenant(id),
            subject_id UUID NOT NULL REFERENCES users(user_id),
            role       VARCHAR(8) NOT NULL
                CHECK (role IN ('OWNER','ADMIN','MEMBER','AUDITOR','SERVICE')),
            state      VARCHAR(10) NOT NULL DEFAULT 'ACTIVE'
                CHECK (state IN ('ACTIVE','SUSPENDED','REVOKED')),
            revision   INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            created_by UUID,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_tenant_membership_active "
        "ON tenant_membership(tenant_id, subject_id) WHERE state = 'ACTIVE'"
    )
    op.execute("CREATE INDEX idx_tenant_membership_subject ON tenant_membership(subject_id)")

    op.execute("INSERT INTO tenant (id, kind) SELECT user_id, 'PERSONAL' FROM users")
    op.execute(
        "INSERT INTO tenant_membership (tenant_id, subject_id, role, created_by) "
        "SELECT user_id, user_id, 'OWNER', user_id FROM users"
    )


def downgrade() -> None:
    op.execute("DROP TABLE tenant_membership")
    op.execute("DROP TABLE tenant")
