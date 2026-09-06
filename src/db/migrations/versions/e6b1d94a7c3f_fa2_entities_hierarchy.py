"""FA-2 — 엔티티 계층 4테이블(legal_entity/fund/portfolio/sub_account).

Revision ID: e6b1d94a7c3f
Revises: c1f4a9e7b3d6
Create Date: 2026-09-06 12:00:00.000000

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-2
(§2.1 표·§3 계약). 착수 시점 `alembic heads`가 단일(c1f4a9e7b3d6)임을
확인하고 그 값을 down_revision으로 썼다(task-1704 decision).

`src/foundation/entities/contracts/v1.py`(FA-1)의 4개 pydantic 모델을
그대로 물리 스키마로 옮긴다 — 필드 집합·nullable 여부가 계약과 정확히
일치해야 한다. `tenant_id`는 `legal_entity`에만 있다(계약에 Fund/
Portfolio/SubAccount는 tenant_id 필드가 없음) — 하위 테이블의 교차
테넌트 격리는 `legal_entity`까지의 FK 체인을 JOIN으로 타는
`adapters/postgres_repository.py`의 책임이다(LA-22/PLT-27 선례,
파라미터가 아닌 필수 인자로 tenant_id를 받아 구조적으로 차단).

엔티티 폐쇄(`closed_at`)는 UPDATE로 이뤄지는 정상 가변 상태다(WORM
아님) — FA-A2(상태성 테이블 UPDATE 금지)의 대상은 ledger/positions
같은 금전 상태이지, 이 계층 메타데이터가 아니다.

`mandate_ref`는 FND-02(`d8e8e4ba2365`)가 만든 `portfolio_mandate(id)`를
참조한다 — nullable(계약과 동일, "펀드 생성 시점에 mandate가 아직 없을
수 있음").
"""
from collections.abc import Sequence
from enum import Enum

from alembic import op

from src.data.models.base import Currency

# revision identifiers, used by Alembic.
revision: str = "e6b1d94a7c3f"
down_revision: str | Sequence[str] | None = "c1f4a9e7b3d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "aios_app"
_TABLES = ("legal_entity", "fund", "portfolio", "sub_account")


def _sql_enum_members(enum_cls: type[Enum]) -> str:
    return ", ".join(f"'{member.value}'" for member in enum_cls)


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE legal_entity (
            entity_id    UUID PRIMARY KEY,
            tenant_id    UUID NOT NULL REFERENCES users(user_id),
            name         TEXT NOT NULL,
            jurisdiction TEXT NOT NULL,
            region_tag   TEXT NOT NULL,
            closed_at    TIMESTAMPTZ
        )
        """
    )
    op.execute("CREATE INDEX ix_legal_entity_tenant_id ON legal_entity (tenant_id)")

    op.execute(
        f"""
        CREATE TABLE fund (
            fund_id       UUID PRIMARY KEY,
            entity_id     UUID NOT NULL REFERENCES legal_entity(entity_id),
            base_currency VARCHAR(10) NOT NULL
                CHECK (base_currency IN ({_sql_enum_members(Currency)})),
            mandate_ref   UUID REFERENCES portfolio_mandate(id),
            inception     DATE NOT NULL,
            closed_at     TIMESTAMPTZ
        )
        """
    )
    op.execute("CREATE INDEX ix_fund_entity_id ON fund (entity_id)")

    op.execute(
        """
        CREATE TABLE portfolio (
            portfolio_id      UUID PRIMARY KEY,
            fund_id           UUID NOT NULL REFERENCES fund(fund_id),
            venue_account_ref TEXT NOT NULL,
            closed_at         TIMESTAMPTZ
        )
        """
    )
    op.execute("CREATE INDEX ix_portfolio_fund_id ON portfolio (fund_id)")

    op.execute(
        """
        CREATE TABLE sub_account (
            sub_account_id UUID PRIMARY KEY,
            portfolio_id   UUID NOT NULL REFERENCES portfolio(portfolio_id),
            owner_ref      UUID NOT NULL REFERENCES users(user_id),
            closed_at      TIMESTAMPTZ
        )
        """
    )
    op.execute("CREATE INDEX ix_sub_account_portfolio_id ON sub_account (portfolio_id)")

    for table in _TABLES:
        op.execute(f"GRANT SELECT, INSERT, UPDATE ON {table} TO {_APP_ROLE}")


def downgrade() -> None:
    op.execute("DROP TABLE sub_account")
    op.execute("DROP TABLE portfolio")
    op.execute("DROP TABLE fund")
    op.execute("DROP TABLE legal_entity")
