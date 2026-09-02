"""foundation trust core — disclosure / consent_record (FND-01)

Revision ID: 84b7d0faf14f
Revises: 8e22a459e6ab
Create Date: 2026-09-02 00:00:00.000000

Spec: AIOSproject 43_trust_core_identity_consent_suitability_specification_v1.0.md,
73_trust_core_l3_build_and_operational_specification_v1.0.md §2.1/§7,
71_mihwa_aios_foundation_implementation_work_packages_v1.0.md FND-01.

편차(스콥 축소, 정직하게 명시): 73번은 tenant_membership/suitability_profile도
FND-01 범위로 명시하지만, 이 리프는 그 둘을 의도적으로 제외한다.

- tenant_membership: 지금 이 코드베이스는 개인 계정만 지원하고(organization/
  household 없음, 66번 문서가 후속 범위), tenant마다 구성원이 정확히 하나뿐이라
  grant/suspend/revoke 상태 머신을 지금 만들면 아무도 호출하지 않는 죽은 코드가
  된다(35번 §9.2 원칙). `TenantContext.tenant_id`는 `users.user_id`로 그대로
  대체한다 — 새 테이블이 필요 없다.
- suitability_profile: `src/services/suitability_questionnaire.py`(FD-15.1)가
  이미 존재하는 별개 구현 경로다(순수 채점 함수 + `users.risk_profile` 컬럼).
  여기서 새 테이블/상태머신을 또 만들면 71번 §1 "기존 legacy 코드를 리팩터링
  없이 감싼다" 원칙과 정면으로 부딪힌다. Trust Core의 suitability freshness
  판단은 별도 PR에서 이 기존 경로를 감싸는 방식으로 추가한다(이 리프의 스콥
  아님).

이번 리프는 disclosure/consent만 다룬다 — Trust Core 중 지금 코드베이스에
정말로 없는, 순수하게 새로운 부분이다.
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "84b7d0faf14f"
down_revision: str | None = "8e22a459e6ab"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE disclosure (
            id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            purpose       VARCHAR(100) NOT NULL,
            revision      INT NOT NULL CHECK (revision > 0),
            content_hash  VARCHAR(128) NOT NULL,
            published_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            retired_at    TIMESTAMPTZ,
            UNIQUE (purpose, revision)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE consent_record (
            id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id             UUID NOT NULL REFERENCES users(user_id),
            subject_id            UUID NOT NULL REFERENCES users(user_id),
            purpose               VARCHAR(100) NOT NULL,
            disclosure_id         UUID NOT NULL REFERENCES disclosure(id),
            disclosure_revision   INT NOT NULL,
            state                 VARCHAR(20) NOT NULL DEFAULT 'ACTIVE'
                CHECK (state IN ('ACTIVE', 'REVOKED')),
            accepted_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
            revoked_at            TIMESTAMPTZ,
            expires_at            TIMESTAMPTZ
        )
        """
    )
    # 73번 §2.1 "unique active purpose per subject/tenant" — 부분 unique index로
    # ACTIVE 상태에서만 강제한다(REVOKED는 append-only 이력이라 여러 개 허용).
    op.execute(
        """
        CREATE UNIQUE INDEX uq_consent_record_active_purpose
        ON consent_record (tenant_id, purpose)
        WHERE state = 'ACTIVE'
        """
    )
    op.execute("CREATE INDEX ix_consent_record_tenant_id ON consent_record (tenant_id)")


def downgrade() -> None:
    op.execute("DROP TABLE consent_record")
    op.execute("DROP TABLE disclosure")
