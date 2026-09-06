"""DC-27 — source_contract(소스 계약 등급·재배포 스코프).

Revision ID: ff56c0e3e1ea
Revises: b5bf8da8e058
Create Date: 2026-09-07

Spec: docs/design/ADR-2026-09-06-H-data-sourcing-self-build-and-contract-tiers.md
D1. `entitlements`(9049e2b6b0b7:100)와 나란히 두는 새 테이블이다 —
`entitlements`는 "테넌트가 벤처를 볼 수 있는가"를, `source_contract`는
"플랫폼이 이 소스를 어떤 등급·재배포 스코프로 맺었는가"를 답한다(서로
다른 축이라 같은 테이블로 합치지 않는다, D1 "새 컨텍스트를 만들지
않는다"는 도메인 코드 계층 얘기이지 테이블 병합을 요구하지 않는다).

`source_id`가 PK다 — D1 "등급 승격은 행 갱신이다": 개인 계약을 기업
계약으로 바꿀 때 새 행이 아니라 이 행의 UPDATE 하나여야 어댑터 코드가
전혀 바뀌지 않는다는 DoD가 성립한다. `credential_ref`는 키링 핸들
문자열만 저장한다 — 원문 키는 이 테이블에 없다(D1 "실제 키는 여기
없다").

`capability`는 JSONB(자산군·해상도·기업행위 유무, D6) — 소스마다
어휘가 달라 CHECK 제약으로 강제할 고정 열거값이 없다.
"""
from collections.abc import Sequence
from enum import Enum

from alembic import op

from src.foundation.market_data.domain.entitlement.source_contract import (
    RedistributionScope,
    SourceContractTier,
)

# revision identifiers, used by Alembic.
revision: str = "ff56c0e3e1ea"
down_revision: str | Sequence[str] | None = "b5bf8da8e058"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "aios_app"


def _sql_enum_members(enum_cls: type[Enum]) -> str:
    return ", ".join(f"'{member.value}'" for member in enum_cls)


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE source_contract (
            source_id             VARCHAR(64) PRIMARY KEY,
            tier                  VARCHAR(20) NOT NULL
                CHECK (tier IN ({_sql_enum_members(SourceContractTier)})),
            credential_ref        VARCHAR(255) NOT NULL,
            redistribution_scope  VARCHAR(20) NOT NULL DEFAULT 'NONE'
                CHECK (redistribution_scope IN ({_sql_enum_members(RedistributionScope)})),
            rate_limit            INTEGER NOT NULL CHECK (rate_limit >= 0),
            quota                 INTEGER NOT NULL CHECK (quota >= 0),
            valid_from            TIMESTAMPTZ NOT NULL,
            valid_to              TIMESTAMPTZ,
            capability            JSONB NOT NULL,
            updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
            CHECK (valid_to IS NULL OR valid_to > valid_from)
        )
        """
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON source_contract TO {_APP_ROLE}")


def downgrade() -> None:
    op.execute("DROP TABLE source_contract")
