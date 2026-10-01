"""FA-2 통합테스트 공용 픽스처.

`tests/conftest.py`가 `TEST_DATABASE_URL`을 `DATABASE_URL` 환경변수로
옮겨 두므로, 여기서는 asyncpg DSN 변환과 4단 계층을 한 번에 만드는
헬퍼만 둔다(`build_hierarchy` — `LegalEntity`부터 `SubAccount`까지
저장소를 통해 실제로 INSERT까지 마친 상태를 반환한다).

DEEPEN task-9240(원 리프 task-6704) — 이 파일 자체는 negative test 0건
(<3), 실패주입/성능단언 마커도 없었다(task-4084 DEEPEN 기준). 아래
`test_*` 함수들은 `build_hierarchy`/`_asyncpg_dsn` 헬퍼가 불변식 위반
입력(존재하지 않는 tenant_id, `tenant` 테이블에 없는 id, DATABASE_URL
미설정)을 조용히 삼키지 않고 명시적으로 거부하는지, 그리고 계층 생성
도중 하위 의존성이 실패했을 때도 fail-closed로 그대로 전파하는지를
`test_tenant_fk_enforced.py`(FA-2a)와 동일한 패턴으로 검증한다."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime, timezone
from uuid import UUID, uuid4

import asyncpg
import pytest

from src.data.models.base import Currency
from src.foundation.entities.adapters.postgres_repository import PostgresEntityRepository
from src.foundation.entities.contracts.v1 import Fund, LegalEntity, Portfolio, SubAccount
from tests.integration.conftest import create_test_tenant


def _asyncpg_dsn() -> str:
    url = os.environ["DATABASE_URL"]
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=8)
    yield p
    await p.close()


@pytest.fixture
def repo(pool: asyncpg.Pool) -> PostgresEntityRepository:
    return PostgresEntityRepository(pool)


@dataclass(frozen=True)
class SeededHierarchy:
    tenant_id: UUID
    legal_entity: LegalEntity
    fund: Fund
    portfolio: Portfolio
    sub_account: SubAccount


async def build_hierarchy(
    pool: asyncpg.Pool, repo: PostgresEntityRepository, *, tenant_id: UUID | None = None
) -> SeededHierarchy:
    tenant_id = tenant_id if tenant_id is not None else await create_test_tenant(pool)
    entity = await repo.create_legal_entity(
        LegalEntity(
            entity_id=uuid4(),
            tenant_id=tenant_id,
            name="Test Legal Entity",
            jurisdiction="KR",
            region_tag="kr-seoul",
        )
    )
    fund = await repo.create_fund(
        Fund(
            fund_id=uuid4(),
            entity_id=entity.entity_id,
            base_currency=Currency.USDT,
            inception=date(2026, 1, 1),
        )
    )
    portfolio = await repo.create_portfolio(
        Portfolio(
            portfolio_id=uuid4(),
            fund_id=fund.fund_id,
            venue_account_ref="venue-acct-1",
        )
    )
    sub_account = await repo.create_sub_account(
        SubAccount(
            sub_account_id=uuid4(),
            portfolio_id=portfolio.portfolio_id,
            owner_ref=tenant_id,
        )
    )
    return SeededHierarchy(
        tenant_id=tenant_id,
        legal_entity=entity,
        fund=fund,
        portfolio=portfolio,
        sub_account=sub_account,
    )


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
