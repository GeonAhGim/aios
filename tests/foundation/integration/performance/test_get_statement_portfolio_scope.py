"""FA-6 — `application/get_statement.py`의 선택적 `portfolio_id` 스코프.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-6
(§9 표 120행, "기존 단일계좌 응답 무변경").

`performance_statement`에는 아직 portfolio_id 컬럼이 없다(get_statement.py
모듈 docstring 참조) — 이 테스트는 (1) `portfolio_id`를 생략하면 이전 리프와
동일하게 동작하고(회귀), (2) tenant의 FA-1 기본 포트폴리오를 주면 통과하며,
(3) 존재하지 않거나 다른 tenant 소유인 portfolio_id는 fail-closed로 거부됨을
실제 DB(TEST_DATABASE_URL)로 확인한다."""

from __future__ import annotations

import asyncio
import math
import os
import time
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

import asyncpg
import pytest

from src.data.models.base import Currency
from src.foundation.entities.adapters.postgres_repository import PostgresEntityRepository
from src.foundation.entities.application.resolve_context import EntityContextResolutionError
from src.foundation.entities.domain.defaults import build_default_hierarchy, default_portfolio_id
from src.foundation.performance.adapters.postgres_repository import (
    PostgresPerformanceRepository,
)
from src.foundation.performance.application.get_statement import get_statement, list_statements
from src.foundation.performance.domain.methodology import DEFAULT_METHODOLOGY
from src.foundation.performance.domain.models import (
    ComponentBreakdown,
    PerformanceStatement,
    ReturnFigure,
    StatementState,
)
from tests.integration.conftest import create_test_tenant
from tests.integration.foundation.entities.conftest import build_hierarchy

_NOW = datetime.now(timezone.utc)


def _asyncpg_dsn() -> str:
    return os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=4)
    yield p
    await p.close()


@pytest.fixture
def perf_repo(pool: asyncpg.Pool) -> PostgresPerformanceRepository:
    return PostgresPerformanceRepository(pool)


@pytest.fixture
def entity_repo(pool: asyncpg.Pool) -> PostgresEntityRepository:
    return PostgresEntityRepository(pool)


def _breakdown() -> ComponentBreakdown:
    return ComponentBreakdown(
        gross_pnl=Decimal("120"),
        fees=Decimal("10"),
        slippage=Decimal("5"),
        funding=Decimal("2"),
        fx=Decimal("0"),
        cashflows_net=Decimal("1000"),
        estimated_tax=Decimal("3"),
        net_pnl=Decimal("100"),
    )


async def _statement(tenant_id, **overrides) -> PerformanceStatement:
    defaults = dict(
        id=uuid4(),
        tenant_id=tenant_id,
        scope="PAPER",
        scope_ref=str(tenant_id),
        period_start=_NOW,
        period_end=_NOW,
        as_of=_NOW,
        methodology_version=DEFAULT_METHODOLOGY.version,
        methodology_hash=DEFAULT_METHODOLOGY.methodology_hash,
        input_refs=("snapshot:abc",),
        components=_breakdown(),
        returns=(
            ReturnFigure(
                value_pct=Decimal("0.21"),
                basis="NET",
                method="TWR",
                period_start=_NOW,
                period_end=_NOW,
                annualized=False,
                periods_per_year=None,
            ),
        ),
        risk={"vol_pct": Decimal("0.05"), "sharpe": None},
        benchmark=None,
        benchmark_ref=None,
        state=StatementState.ESTIMATED,
        revision_no=1,
        prior_statement_id=None,
        identity_ok=True,
        identity_residual=Decimal("0"),
        limitations=(),
        evidence_refs=("audit:1",),
    )
    defaults.update(overrides)
    return PerformanceStatement(**defaults)


async def _seed_default_hierarchy(
    pool: asyncpg.Pool, repo: PostgresEntityRepository, tenant_id
) -> None:
    """FA-1 결정론(UUIDv5) 기본 계층을 실제로 영속화한다 — `build_hierarchy`
    (entities conftest)는 무작위 id를 쓰므로, `default_portfolio_id(tenant_id)`와
    일치하는 포트폴리오가 필요한 이 테스트에는 맞지 않는다."""
    hierarchy = build_default_hierarchy(
        user_id=tenant_id,
        tenant_id=tenant_id,
        base_currency=Currency.USDT,
        jurisdiction="KR",
        region_tag="kr-seoul",
        venue_account_ref="venue-acct-1",
        inception=date(2026, 1, 1),
    )
    await repo.create_legal_entity(hierarchy.legal_entity)
    await repo.create_fund(hierarchy.fund)
    await repo.create_portfolio(hierarchy.portfolio)
    await repo.create_sub_account(hierarchy.sub_account)


async def test_get_and_list_statements_without_portfolio_id_is_unchanged_regression(
    pool, perf_repo
):
    tenant_id = await create_test_tenant(pool)
    await perf_repo.insert_methodology(DEFAULT_METHODOLOGY)
    inserted = await perf_repo.insert_statement(await _statement(tenant_id))

    got = await get_statement(perf_repo, tenant_id=tenant_id, statement_id=inserted.id)
    listed = await list_statements(perf_repo, tenant_id=tenant_id)

    assert got.id == inserted.id
    assert [s.id for s in listed] == [inserted.id]


async def test_get_and_list_statements_accept_tenant_default_portfolio_id(
    pool, perf_repo, entity_repo
):
    tenant_id = await create_test_tenant(pool, bootstrap_default_hierarchy_rows=False)
    await _seed_default_hierarchy(pool, entity_repo, tenant_id)
    await perf_repo.insert_methodology(DEFAULT_METHODOLOGY)
    inserted = await perf_repo.insert_statement(await _statement(tenant_id))
    portfolio_id = default_portfolio_id(tenant_id)

    got = await get_statement(
        perf_repo,
        tenant_id=tenant_id,
        statement_id=inserted.id,
        portfolio_id=portfolio_id,
        entities=entity_repo,
    )
    listed = await list_statements(
        perf_repo, tenant_id=tenant_id, portfolio_id=portfolio_id, entities=entity_repo
    )

    assert got.id == inserted.id
    assert [s.id for s in listed] == [inserted.id]


async def test_get_statement_rejects_other_portfolio_id_fail_closed(pool, perf_repo, entity_repo):
    """negative — statement가 귀속되지 않은(오늘의 유일한 귀속처가 아닌)
    portfolio_id를 주면 tenant 전체를 돌려주는 대신 거부한다."""
    tenant_id = await create_test_tenant(pool)
    hierarchy = await build_hierarchy(pool, entity_repo, tenant_id=tenant_id)
    await perf_repo.insert_methodology(DEFAULT_METHODOLOGY)
    inserted = await perf_repo.insert_statement(await _statement(tenant_id))

    with pytest.raises(EntityContextResolutionError):
        await get_statement(
            perf_repo,
            tenant_id=tenant_id,
            statement_id=inserted.id,
            portfolio_id=hierarchy.portfolio.portfolio_id,
            entities=entity_repo,
        )


async def test_get_statement_rejects_cross_tenant_portfolio_id_fail_closed(
    pool, perf_repo, entity_repo
):
    """negative — 다른 tenant가 실제로 소유한 portfolio_id를 주면(값 자체는
    유효) 전체 반환 대신 거부한다(교차 테넌트 유출 방지)."""
    victim_tenant = await create_test_tenant(pool)
    victim_hierarchy = await build_hierarchy(pool, entity_repo, tenant_id=victim_tenant)
    await perf_repo.insert_methodology(DEFAULT_METHODOLOGY)

    attacker_tenant = await create_test_tenant(pool)
    inserted = await perf_repo.insert_statement(await _statement(attacker_tenant))

    with pytest.raises(EntityContextResolutionError):
        await get_statement(
            perf_repo,
            tenant_id=attacker_tenant,
            statement_id=inserted.id,
            portfolio_id=victim_hierarchy.portfolio.portfolio_id,
            entities=entity_repo,
        )


async def test_get_statement_mid_scope_infra_failure_propagates_fail_closed(
    pool, perf_repo, entity_repo, monkeypatch
):
    """실패주입+게이트재현 — DEPTH 감사(task-2724)가 지적한 공백: 이 파일의
    기존 negative는 전부 "존재하지 않음/다른 tenant 소유" 같은 정상 입력
    검증 거부였을 뿐, entities 저장소 자체가 커넥션 예외로 죽는 경우는 한
    번도 흉내내지 않았다. portfolio 조회는 성공하지만 그 다음 단계
    (`get_fund`, `resolve_portfolio_scope` 내부)에서 인프라 장애가 나면,
    `get_statement`는 이미 부분적으로 확인된 스코프를 근거로 tenant 전체를
    돌려주는 폴백 없이 그 예외를 그대로 전파해야 한다."""
    tenant_id = await create_test_tenant(pool, bootstrap_default_hierarchy_rows=False)
    await _seed_default_hierarchy(pool, entity_repo, tenant_id)
    await perf_repo.insert_methodology(DEFAULT_METHODOLOGY)
    inserted = await perf_repo.insert_statement(await _statement(tenant_id))
    portfolio_id = default_portfolio_id(tenant_id)

    real_get_fund = entity_repo.get_fund

    async def _flaky_get_fund(tenant_id, fund_id):
        raise asyncpg.PostgresConnectionError("simulated entities adapter outage")

    monkeypatch.setattr(entity_repo, "get_fund", _flaky_get_fund)

    with pytest.raises(asyncpg.PostgresConnectionError):
        await get_statement(
            perf_repo,
            tenant_id=tenant_id,
            statement_id=inserted.id,
            portfolio_id=portfolio_id,
            entities=entity_repo,
        )

    monkeypatch.setattr(entity_repo, "get_fund", real_get_fund)
    got = await get_statement(
        perf_repo,
        tenant_id=tenant_id,
        statement_id=inserted.id,
        portfolio_id=portfolio_id,
        entities=entity_repo,
    )
    assert got.id == inserted.id


async def test_get_statement_concurrent_mixed_tenants_do_not_cross_leak(
    pool, perf_repo, entity_repo
):
    """D3증거 — 서로 다른 tenant의 `get_statement(portfolio_id=...)` 호출을
    asyncio.gather로 동시에 섞어 실행해도(공유 커넥션 풀·저장소 인스턴스)
    각 호출은 자신의 tenant_id/portfolio_id 기준으로만 판정된다 — 동시
    실행이 만드는 경합으로 한 tenant의 statement가 다른 tenant에게 새는
    사고(교차 유출)가 없음을 증명한다."""
    await perf_repo.insert_methodology(DEFAULT_METHODOLOGY)
    tenants = []
    for _ in range(4):
        tenant_id = await create_test_tenant(pool, bootstrap_default_hierarchy_rows=False)
        await _seed_default_hierarchy(pool, entity_repo, tenant_id)
        inserted = await perf_repo.insert_statement(await _statement(tenant_id))
        tenants.append((tenant_id, inserted.id))

    async def _get(tenant_id, statement_id):
        return await get_statement(
            perf_repo,
            tenant_id=tenant_id,
            statement_id=statement_id,
            portfolio_id=default_portfolio_id(tenant_id),
            entities=entity_repo,
        )

    calls = [_get(tenant_id, statement_id) for tenant_id, statement_id in tenants for _ in range(3)]
    results = await asyncio.gather(*calls)

    expected_ids = [statement_id for _, statement_id in tenants for _ in range(3)]
    assert [r.id for r in results] == expected_ids


@pytest.mark.perf
async def test_get_statement_portfolio_scope_p95_latency_stays_within_normalized_ceiling(
    pool, perf_repo, entity_repo
):
    """수치 성능 단언 — 공유 TEST_DATABASE_URL의 절대 지연 변동성 때문에
    절대 ms 임계 대신, 가벼운 baseline 호출 1건 대비 정규화한 상한만
    게이트로 쓴다(task-3009 test_resolve_context_performance.py와 동일
    교훈). `portfolio_id` 경로는 `resolve_portfolio_scope`가 추가하는 3회
    라운드트립(get_portfolio/get_fund/get_legal_entity)만큼 무변경 경로보다
    비용이 늘어야 정상이므로, 그 고정 비용이 회귀로 자라는지 감시한다."""
    tenant_id = await create_test_tenant(pool, bootstrap_default_hierarchy_rows=False)
    await _seed_default_hierarchy(pool, entity_repo, tenant_id)
    await perf_repo.insert_methodology(DEFAULT_METHODOLOGY)
    inserted = await perf_repo.insert_statement(await _statement(tenant_id))
    portfolio_id = default_portfolio_id(tenant_id)

    async def _call() -> float:
        start = time.perf_counter()
        await get_statement(
            perf_repo,
            tenant_id=tenant_id,
            statement_id=inserted.id,
            portfolio_id=portfolio_id,
            entities=entity_repo,
        )
        return time.perf_counter() - start

    baseline_elapsed = await _call()
    samples = sorted([await _call() for _ in range(30)])
    p95 = samples[math.ceil(0.95 * len(samples)) - 1]

    ceiling = baseline_elapsed * 5 + 0.05
    assert p95 <= ceiling, (
        f"get_statement(portfolio_id=...) p95 지연 {p95:.4f}s가 정규화 상한 "
        f"{ceiling:.4f}s(baseline {baseline_elapsed:.4f}s)를 초과했습니다 -- "
        "resolve_portfolio_scope 라운드트립 회귀 의심"
    )
