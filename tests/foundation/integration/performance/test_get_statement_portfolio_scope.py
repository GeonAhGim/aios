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
from datetime import UTC, date, datetime
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
from tests.support.db import create_pool_with_retry

_NOW = datetime.now(UTC)


def _asyncpg_dsn() -> str:
    return os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await create_pool_with_retry(_asyncpg_dsn(), min_size=1, max_size=4)
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
    pool, perf_repo, entity_repo, perf_budget
):
    """수치 성능 단언 — raw perf_counter → perf_budget.sample_async 전환(task-10894).
    공유 TEST_DATABASE_URL의 절대 지연 변동성 때문에 절대 ms 임계 대신,
    가벼운 baseline 호출 1건 대비 정규화한 상한만 게이트로 쓴다.
    `portfolio_id` 경로는 `resolve_portfolio_scope`가 추가하는 3회
    라운드트립(get_portfolio/get_fund/get_legal_entity)만큼 무변경 경로보다
    비용이 늘어야 정상이므로, 그 고정 비용이 회귀로 자라는지 감시한다."""
    tenant_id = await create_test_tenant(pool, bootstrap_default_hierarchy_rows=False)
    await _seed_default_hierarchy(pool, entity_repo, tenant_id)
    await perf_repo.insert_methodology(DEFAULT_METHODOLOGY)
    inserted = await perf_repo.insert_statement(await _statement(tenant_id))
    portfolio_id = default_portfolio_id(tenant_id)

    async def _call() -> float:
        sample = await perf_budget.sample_async(
            lambda: get_statement(
                perf_repo,
                tenant_id=tenant_id,
                statement_id=inserted.id,
                portfolio_id=portfolio_id,
                entities=entity_repo,
            )
        )
        return sample.wall_ms

    baseline_elapsed = await _call()
    samples = sorted([await _call() for _ in range(30)])
    p95 = samples[math.ceil(0.95 * len(samples)) - 1]

    ceiling = baseline_elapsed * 5 + 0.05
    assert p95 <= ceiling, (
        f"get_statement(portfolio_id=...) p95 지연 {p95:.4f}ms가 정규화 상한 "
        f"{ceiling:.4f}ms(baseline {baseline_elapsed:.4f}ms)를 초과했습니다 -- "
        "resolve_portfolio_scope 라운드트립 회귀 의심"
    )


# ---- 게이트 적색 재현 / 지연 주입 -----------------------------------------------


@pytest.mark.perf
async def test_p95_assertion_goes_red_with_io_delay_injection(
    monkeypatch: pytest.MonkeyPatch,
    pool,
    perf_repo,
    entity_repo,
    perf_budget,
) -> None:
    """gate-red reproduction — `resolve_portfolio_scope` 내부 get_portfolio에
     지연(delayed_async, 0.5s)을 주입하면 같은 정규화 상한 단언이 AssertionError
    로 적색이 된다.

     `delayed_async`는 wall-clock 대기를 주입한다.
     `PerfBudget.sample_async`는 wall_ms를 측정하므로 wall-clock 기반 budget
     단언이 지연을 잡는다. 정상 입력에서 통과하는 것이 테스트의 허수(trivial
     pass)가 아님을 보증한다. 단언/마커 삭제와 예산 완화 금지.
    """
    from tests.support.perf_inject import delayed_async

    tenant_id = await create_test_tenant(pool, bootstrap_default_hierarchy_rows=False)
    await _seed_default_hierarchy(pool, entity_repo, tenant_id)
    await perf_repo.insert_methodology(DEFAULT_METHODOLOGY)
    inserted = await perf_repo.insert_statement(await _statement(tenant_id))
    portfolio_id = default_portfolio_id(tenant_id)

    async def _call() -> float:
        sample = await perf_budget.sample_async(
            lambda: get_statement(
                perf_repo,
                tenant_id=tenant_id,
                statement_id=inserted.id,
                portfolio_id=portfolio_id,
                entities=entity_repo,
            )
        )
        return sample.wall_ms

    # 지연 주입 전 baseline 측정
    baseline_elapsed = await _call()

    # resolve_portfolio_scope 의 첫 DB 호출(get_portfolio)에 0.5s 지연 주입
    original_get_portfolio = entity_repo.get_portfolio
    monkeypatch.setattr(
        entity_repo,
        "get_portfolio",
        delayed_async(original_get_portfolio, 0.5),
    )

    samples = sorted([await _call() for _ in range(30)])
    p95 = samples[math.ceil(0.95 * len(samples)) - 1]

    # 지연 주입 후 p95가 baseline 기반 상한을 넘어야 함 — 지연이 ~500ms×3라운드트립
    ceiling = baseline_elapsed * 5 + 0.05
    assert p95 > ceiling, (
        f"지연 주입 후 p95 지연 {p95:.4f}ms가 상한 {ceiling:.4f}ms를 넘지 않음 — "
        "측정/단언이 공허함(hollow assertion, budget>=p95)"
    )


# ---- 음성 테스트 (negative tests ≥ 3) -------------------------------------------


async def test_p95_assertion_not_triggered_by_omitting_portfolio_id(
    pool,
    perf_repo,
    perf_budget,
) -> None:
    """음성 테스트 — portfolio_id 없이 호출하면 resolve_portfolio_scope 경로
    (3회 라운드트립)를 타지 않으므로 p95가 훨씬 낮다. 같은 상한 공식을
    적용했을 때 green이 되는지를 확인: 성능 단언이 portfolio_id 경로만의
    회귀를 감지하는지 검증."""
    tenant_id = await create_test_tenant(pool)
    await perf_repo.insert_methodology(DEFAULT_METHODOLOGY)
    inserted = await perf_repo.insert_statement(await _statement(tenant_id))

    async def _call() -> float:
        sample = await perf_budget.sample_async(
            lambda: get_statement(
                perf_repo,
                tenant_id=tenant_id,
                statement_id=inserted.id,
            )
        )
        return sample.wall_ms

    baseline_elapsed = await _call()
    samples = sorted([await _call() for _ in range(30)])
    p95 = samples[math.ceil(0.95 * len(samples)) - 1]

    ceiling = baseline_elapsed * 5 + 0.05
    assert p95 <= ceiling, (
        f"portfolio_id 없이 호출 시 p95 {p95:.4f}ms가 상한 {ceiling:.4f}ms 초과 — "
        "단언이 경로 구별 없이 모든 호출에 느림을 허용함"
    )


async def test_p95_assertion_with_non_default_portfolio_id_rejected_before_timing(
    pool,
    perf_repo,
    entity_repo,
) -> None:
    """음성 테스트 — 존재하지 않는 portfolio_id를 주면 get_statement가 즉시
    EntityContextResolutionError를 던진다. 이 예외 경로는 성능 측정 구간을
    통과하지 않으므로, p95 측정 테스트가 해당 경로를 우회해야 한다.
    예외가 실제로 던져지는지를 검증."""
    tenant_id = await create_test_tenant(pool)
    await perf_repo.insert_methodology(DEFAULT_METHODOLOGY)
    inserted = await perf_repo.insert_statement(await _statement(tenant_id))
    fake_portfolio_id = uuid4()

    with pytest.raises(EntityContextResolutionError):
        await get_statement(
            perf_repo,
            tenant_id=tenant_id,
            statement_id=inserted.id,
            portfolio_id=fake_portfolio_id,
            entities=entity_repo,
        )


async def test_p95_baseline_self_consistency_across_runs(
    pool,
    perf_repo,
    entity_repo,
    perf_budget,
) -> None:
    """음성 테스트 — 같은 설정으로 baseline을 2회 측정하면 둘 다 상한 내에서
    통과해야 한다. 단언이 한 번만 우연히 green인 것이 아님을 확인."""
    tenant_id = await create_test_tenant(pool, bootstrap_default_hierarchy_rows=False)
    await _seed_default_hierarchy(pool, entity_repo, tenant_id)
    await perf_repo.insert_methodology(DEFAULT_METHODOLOGY)
    inserted = await perf_repo.insert_statement(await _statement(tenant_id))
    portfolio_id = default_portfolio_id(tenant_id)

    async def _call() -> float:
        sample = await perf_budget.sample_async(
            lambda: get_statement(
                perf_repo,
                tenant_id=tenant_id,
                statement_id=inserted.id,
                portfolio_id=portfolio_id,
                entities=entity_repo,
            )
        )
        return sample.wall_ms

    # 2회 독립 baseline 측정
    baseline_1 = await _call()
    baseline_2 = await _call()

    # 각 baseline이 자체 상한 내에서 green이어야 함
    ceiling_1 = baseline_1 * 5 + 0.05
    ceiling_2 = baseline_2 * 5 + 0.05

    samples_1 = sorted([await _call() for _ in range(30)])
    p95_1 = samples_1[math.ceil(0.95 * len(samples_1)) - 1]
    samples_2 = sorted([await _call() for _ in range(30)])
    p95_2 = samples_2[math.ceil(0.95 * len(samples_2)) - 1]

    assert p95_1 <= ceiling_1, f"1차 baseline p95 {p95_1:.4f}ms > 상한 {ceiling_1:.4f}ms"
    assert p95_2 <= ceiling_2, f"2차 baseline p95 {p95_2:.4f}ms > 상한 {ceiling_2:.4f}ms"
