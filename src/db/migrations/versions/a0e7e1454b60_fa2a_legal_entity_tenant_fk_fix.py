"""FA-2a — 소급 교정: legal_entity.tenant_id를 tenant(id)로 교체.

Revision ID: a0e7e1454b60
Revises: e6b1d94a7c3f
Create Date: 2026-09-06 22:27:53.006560

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-2a
ADR-2026-09-06-G §2 D0. `e6b1d94a7c3f:53`이 오늘 병합되면서
`legal_entity.tenant_id UUID NOT NULL REFERENCES users(user_id)`를
만들었다 — ADR-2026-09-06-E가 FA-0a를 신설한 결함(테넌트 FK가
`users`를 가리키는 패턴)의 재생산이다. 실제 테넌트 테이블은
`f4a6b8c0d2e4:27`의 `tenant(id)`.

재매핑: `f4a6b8c0d2e4`가 기존 모든 사용자에 대해
`id = user_id`인 PERSONAL tenant를 이미 만들어 뒀으므로, FK가
`users(user_id)`였던 기존 `legal_entity.tenant_id` 값은 이미
`tenant(id)`의 유효한 값과 같다 — 정상 케이스는 값 변경이 필요 없다.
그 불변조건이 깨진 행(예: 나중에 tenant가 삭제된 경우)만 첫 번째
tenant(단일 사용자 환경의 기본 tenant)로 재매핑한다.
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a0e7e1454b60"
down_revision: str | Sequence[str] | None = "e6b1d94a7c3f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FK_NAME = "legal_entity_tenant_id_fkey"


def upgrade() -> None:
    op.execute(f"ALTER TABLE legal_entity DROP CONSTRAINT {_FK_NAME}")
    op.execute(
        """
        UPDATE legal_entity
        SET tenant_id = (SELECT id FROM tenant ORDER BY created_at LIMIT 1)
        WHERE tenant_id NOT IN (SELECT id FROM tenant)
        AND EXISTS (SELECT 1 FROM tenant)
        """
    )
    op.execute(
        f"ALTER TABLE legal_entity ADD CONSTRAINT {_FK_NAME} "
        "FOREIGN KEY (tenant_id) REFERENCES tenant(id)"
    )


def downgrade() -> None:
    op.execute(f"ALTER TABLE legal_entity DROP CONSTRAINT {_FK_NAME}")
    op.execute(
        f"ALTER TABLE legal_entity ADD CONSTRAINT {_FK_NAME} "
        "FOREIGN KEY (tenant_id) REFERENCES users(user_id)"
    )
