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
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from uuid import UUID, uuid4

import asyncpg
import pytest

from src.data.models.base import Currency
from src.foundation.entities.adapters.postgres_repository import PostgresEntityRepository
from src.foundation.entities.contracts.v1 import Fund, LegalEntity, Portfolio, SubAccount
from tests.integration.conftest import create_test_tenant, create_test_user


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


async def test_asyncpg_dsn_raises_keyerror_when_database_url_unset(monkeypatch):
    # negative 1/3 — DATABASE_URL이 없으면(`tests/conftest.py`가 옮겨 두지
    # 못했거나, 이 헬퍼가 다른 컨텍스트에서 호출된 경우) 잘못된 기본값으로
    # 조용히 넘어가지 않고 즉시 KeyError로 거부해야 한다(fail-closed).
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(KeyError):
        _asyncpg_dsn()


async def test_build_hierarchy_rejects_nonexistent_explicit_tenant_id(pool, repo):
    # negative 2/3 — `tenant` 테이블에 존재하지 않는 tenant_id를 명시적으로
    # 넘기면, legal_entity.tenant_id FK(FA-2a)가 즉시 거부해야 한다.
    # build_hierarchy가 이 실패를 삼키고 부분 상태를 반환해서는 안 된다.
    missing_tenant_id = uuid4()

    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await build_hierarchy(pool, repo, tenant_id=missing_tenant_id)


async def test_build_hierarchy_rejects_dangling_user_id_without_tenant_row(pool, repo):
    # negative 3/3 — `users`에는 있지만 `tenant`에는 없는 id(예: PERSONAL
    # tenant 백필 이전의 레거시 사용자)도 tenant(id) FK 앞에서는 존재하지
    # 않는 tenant_id와 동일하게 거부되어야 한다(test_tenant_fk_enforced.py
    # 배선제거 증명과 동일 원리).
    dangling_user_id = await create_test_user(pool)

    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await build_hierarchy(pool, repo, tenant_id=dangling_user_id)


async def test_build_hierarchy_propagates_mid_chain_failure_without_swallowing(
    pool, repo, monkeypatch
):
    # 실패주입 — legal_entity까지는 성공하고 그 다음 단계(create_fund)에서
    # 하위 의존성이 예외를 던지는 상황을 흉내낸다. build_hierarchy는 이
    # 예외를 삼켜 가짜 SeededHierarchy를 반환해서는 안 되고(fail-closed),
    # 호출자가 실제로 어디까지 만들어졌는지 알 수 없는 상태로 성공한 척
    # 넘어가면 안 된다.
    original_create_fund = repo.create_fund

    async def _failing_create_fund(*args, **kwargs):
        raise RuntimeError("injected create_fund dependency failure")

    monkeypatch.setattr(repo, "create_fund", _failing_create_fund)

    with pytest.raises(RuntimeError, match="injected create_fund dependency failure"):
        await build_hierarchy(pool, repo)

    monkeypatch.setattr(repo, "create_fund", original_create_fund)


@pytest.mark.perf
async def test_build_hierarchy_p95_latency_stays_within_normalized_ceiling(pool, repo):
    # 성능단언(D2 하한) — build_hierarchy는 거의 모든 FA-2 통합테스트가
    # setup으로 호출하는 4단 INSERT 체인이다. baseline 1회 대비 정규화한
    # 상한만 게이트로 쓰는 이유는 test_postgres_entity_repository.py의
    # 동일 패턴과 같다(공유 TEST_DATABASE_URL의 절대 지연 변동성).
    baseline_start = time.perf_counter()
    await build_hierarchy(pool, repo)
    baseline_elapsed = time.perf_counter() - baseline_start

    samples: list[float] = []
    for _ in range(10):
        start = time.perf_counter()
        await build_hierarchy(pool, repo)
        samples.append(time.perf_counter() - start)

    samples.sort()
    p95 = samples[-1]

    ceiling = baseline_elapsed * 5 + 0.5
    assert p95 <= ceiling, (
        f"build_hierarchy p95 지연 {p95:.4f}s가 정규화 상한 {ceiling:.4f}s"
        f"(baseline {baseline_elapsed:.4f}s)를 초과했습니다 — 4단 INSERT 체인 회귀 의심"
    )
