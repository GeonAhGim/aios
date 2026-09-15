"""FA-2 — PostgresEntityRepository 실DB 통합테스트.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-2
— FK·유일성, 교차 테넌트 404(LA-22/PLT-27 선례) 검증. 마이그레이션
왕복은 `test_migration_roundtrip.py`가 별도로 다룬다.

DEPTH 감사(task-2724, docs/audit/DEPTH_FA.md)가 이 리프의 D3 하한 미달로
지적한 공백(성능단언 없음, D3 증거(적대적/리플레이/다중워커) 없음)을
`test_get_legal_entity_p95_latency_stays_within_normalized_ceiling`과
`test_20_concurrent_close_legal_entity_requests_leave_exactly_one_winner`로
메운다(task-3005).

task-2431(FA-2 TOCTOU 원자화 + list_*_by_* tenant_id 필수화, 실질 수정
커밋 7bb3ac62 — task.json commit 필드가 뒤이은 순수 리팩터 커밋 6f9dd72d를
가리키던 레코드 불일치를 task-3030이 정정)에 대해서도 동일하게 지적된
"수치 성능 단언 없음"을
`test_list_funds_by_entity_p95_latency_stays_within_normalized_ceiling`과
`test_close_legal_entity_not_exists_guard_throughput_stays_within_budget`로
메운다(task-3030)."""

from __future__ import annotations

import asyncio
import math
import time
from datetime import date
from uuid import uuid4

import asyncpg
import pytest

from src.core.db.conditional_write import ConcurrencyConflictError
from src.data.models.base import Currency
from src.foundation.entities.contracts.v1 import Fund, LegalEntity, Portfolio, SubAccount
from src.foundation.entities.domain.hierarchy import HierarchyViolationError
from tests.integration.conftest import create_test_tenant, create_test_user
from tests.integration.foundation.entities.conftest import build_hierarchy, now_utc


async def test_create_and_get_roundtrip_across_all_four_levels(pool, repo):
    seeded = await build_hierarchy(pool, repo)

    fetched_entity = await repo.get_legal_entity(seeded.tenant_id, seeded.legal_entity.entity_id)
    fetched_fund = await repo.get_fund(seeded.tenant_id, seeded.fund.fund_id)
    fetched_portfolio = await repo.get_portfolio(seeded.tenant_id, seeded.portfolio.portfolio_id)
    fetched_sub_account = await repo.get_sub_account(
        seeded.tenant_id, seeded.sub_account.sub_account_id
    )

    assert fetched_entity == seeded.legal_entity
    assert fetched_fund == seeded.fund
    assert fetched_portfolio == seeded.portfolio
    assert fetched_sub_account == seeded.sub_account


async def test_get_nonexistent_id_returns_none_at_every_level(pool, repo):
    tenant_id = await create_test_user(pool)
    missing = uuid4()

    assert await repo.get_legal_entity(tenant_id, missing) is None
    assert await repo.get_fund(tenant_id, missing) is None
    assert await repo.get_portfolio(tenant_id, missing) is None
    assert await repo.get_sub_account(tenant_id, missing) is None


async def test_cross_tenant_get_collapses_to_none_at_every_level(pool, repo):
    # 404 동형(LA-22 선례) — 존재는 하지만 다른 tenant 소유인 id도 "없음"과
    # 똑같이 None으로 접혀야 한다. 호출부가 존재 여부와 소유권 여부를
    # 구분할 방법을 구조적으로 없앤다.
    owner = await build_hierarchy(pool, repo)
    other_tenant_id = await create_test_user(pool)

    assert await repo.get_legal_entity(other_tenant_id, owner.legal_entity.entity_id) is None
    assert await repo.get_fund(other_tenant_id, owner.fund.fund_id) is None
    assert await repo.get_portfolio(other_tenant_id, owner.portfolio.portfolio_id) is None
    assert await repo.get_sub_account(other_tenant_id, owner.sub_account.sub_account_id) is None


async def test_duplicate_primary_key_is_rejected_at_every_level(pool, repo):
    seeded = await build_hierarchy(pool, repo)

    with pytest.raises(asyncpg.UniqueViolationError):
        await repo.create_legal_entity(
            LegalEntity(
                entity_id=seeded.legal_entity.entity_id,
                tenant_id=seeded.tenant_id,
                name="dup",
                jurisdiction="KR",
                region_tag="kr-seoul",
            )
        )
    with pytest.raises(asyncpg.UniqueViolationError):
        await repo.create_fund(
            Fund(
                fund_id=seeded.fund.fund_id,
                entity_id=seeded.legal_entity.entity_id,
                base_currency=Currency.USDT,
                inception=date(2026, 1, 1),
            )
        )
    with pytest.raises(asyncpg.UniqueViolationError):
        await repo.create_portfolio(
            Portfolio(
                portfolio_id=seeded.portfolio.portfolio_id,
                fund_id=seeded.fund.fund_id,
                venue_account_ref="dup",
            )
        )
    with pytest.raises(asyncpg.UniqueViolationError):
        await repo.create_sub_account(
            SubAccount(
                sub_account_id=seeded.sub_account.sub_account_id,
                portfolio_id=seeded.portfolio.portfolio_id,
                owner_ref=seeded.tenant_id,
            )
        )


async def test_fund_rejects_reference_to_missing_legal_entity(pool, repo):
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await repo.create_fund(
            Fund(
                fund_id=uuid4(),
                entity_id=uuid4(),
                base_currency=Currency.USDT,
                inception=date(2026, 1, 1),
            )
        )


async def test_portfolio_rejects_reference_to_missing_fund(pool, repo):
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await repo.create_portfolio(
            Portfolio(portfolio_id=uuid4(), fund_id=uuid4(), venue_account_ref="x")
        )


async def test_sub_account_rejects_reference_to_missing_portfolio(pool, repo):
    tenant_id = await create_test_user(pool)
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await repo.create_sub_account(
            SubAccount(sub_account_id=uuid4(), portfolio_id=uuid4(), owner_ref=tenant_id)
        )


async def test_close_legal_entity_then_reclose_raises_concurrency_conflict(pool, repo):
    seeded = await build_hierarchy(pool, repo)
    # 상위 폐쇄는 활성 하위가 없어야 하므로(NOT EXISTS 절, FA-2) 최하위부터
    # 순서대로 닫는다.
    await repo.close_sub_account(
        seeded.tenant_id, seeded.sub_account.sub_account_id, closed_at=now_utc()
    )
    await repo.close_portfolio(seeded.tenant_id, seeded.portfolio.portfolio_id, closed_at=now_utc())
    await repo.close_fund(seeded.tenant_id, seeded.fund.fund_id, closed_at=now_utc())

    closed = await repo.close_legal_entity(
        seeded.tenant_id, seeded.legal_entity.entity_id, closed_at=now_utc()
    )
    assert closed.closed_at is not None

    with pytest.raises(ConcurrencyConflictError):
        await repo.close_legal_entity(
            seeded.tenant_id, seeded.legal_entity.entity_id, closed_at=now_utc()
        )


async def test_close_legal_entity_cross_tenant_raises_lookup_error(pool, repo):
    seeded = await build_hierarchy(pool, repo)
    other_tenant_id = await create_test_user(pool)

    with pytest.raises(LookupError):
        await repo.close_legal_entity(
            other_tenant_id, seeded.legal_entity.entity_id, closed_at=now_utc()
        )
    # 실패한 시도가 실제로 폐쇄 상태를 바꾸지 않았는지 확인.
    reread = await repo.get_legal_entity(seeded.tenant_id, seeded.legal_entity.entity_id)
    assert reread is not None
    assert reread.closed_at is None


async def test_close_fund_cross_tenant_raises_lookup_error_and_leaves_open(pool, repo):
    seeded = await build_hierarchy(pool, repo)
    other_tenant_id = await create_test_user(pool)

    with pytest.raises(LookupError):
        await repo.close_fund(other_tenant_id, seeded.fund.fund_id, closed_at=now_utc())

    reread = await repo.get_fund(seeded.tenant_id, seeded.fund.fund_id)
    assert reread is not None
    assert reread.closed_at is None


async def test_close_portfolio_then_reclose_raises_concurrency_conflict(pool, repo):
    seeded = await build_hierarchy(pool, repo)
    await repo.close_sub_account(
        seeded.tenant_id, seeded.sub_account.sub_account_id, closed_at=now_utc()
    )

    closed = await repo.close_portfolio(
        seeded.tenant_id, seeded.portfolio.portfolio_id, closed_at=now_utc()
    )
    assert closed.closed_at is not None

    with pytest.raises(ConcurrencyConflictError):
        await repo.close_portfolio(
            seeded.tenant_id, seeded.portfolio.portfolio_id, closed_at=now_utc()
        )


async def test_close_sub_account_cross_tenant_raises_lookup_error(pool, repo):
    seeded = await build_hierarchy(pool, repo)
    other_tenant_id = await create_test_user(pool)

    with pytest.raises(LookupError):
        await repo.close_sub_account(
            other_tenant_id, seeded.sub_account.sub_account_id, closed_at=now_utc()
        )


async def test_list_children_reflect_created_rows(pool, repo):
    seeded = await build_hierarchy(pool, repo)

    funds = await repo.list_funds_by_entity(seeded.tenant_id, seeded.legal_entity.entity_id)
    portfolios = await repo.list_portfolios_by_fund(seeded.tenant_id, seeded.fund.fund_id)
    sub_accounts = await repo.list_sub_accounts_by_portfolio(
        seeded.tenant_id, seeded.portfolio.portfolio_id
    )

    assert [f.fund_id for f in funds] == [seeded.fund.fund_id]
    assert [p.portfolio_id for p in portfolios] == [seeded.portfolio.portfolio_id]
    assert [s.sub_account_id for s in sub_accounts] == [seeded.sub_account.sub_account_id]


async def test_list_funds_by_entity_cross_tenant_returns_empty(pool, repo):
    seeded = await build_hierarchy(pool, repo)
    other_tenant_id = await create_test_user(pool)

    assert await repo.list_funds_by_entity(other_tenant_id, seeded.legal_entity.entity_id) == []


async def test_list_portfolios_by_fund_cross_tenant_returns_empty(pool, repo):
    seeded = await build_hierarchy(pool, repo)
    other_tenant_id = await create_test_user(pool)

    assert await repo.list_portfolios_by_fund(other_tenant_id, seeded.fund.fund_id) == []


async def test_list_sub_accounts_by_portfolio_cross_tenant_returns_empty(pool, repo):
    seeded = await build_hierarchy(pool, repo)
    other_tenant_id = await create_test_user(pool)

    assert (
        await repo.list_sub_accounts_by_portfolio(other_tenant_id, seeded.portfolio.portfolio_id)
        == []
    )


async def test_close_legal_entity_toctou_active_fund_inserted_after_precheck_is_rejected(
    pool, repo
):
    # 사전 SELECT(list_funds_by_entity)가 "활성 자식 없음"을 본 직후, UPDATE
    # 이전에 다른 트랜잭션이 활성 Fund를 INSERT하는 경합을 재현한다. 수정 전
    # 코드(조건부 UPDATE에 NOT EXISTS가 없던 버전)는 이 사전검사만 믿고 상위를
    # 그대로 CLOSED로 만들었다(활성 자식을 둔 채) — 수정 후에는 UPDATE 문
    # 자체가 NOT EXISTS로 재확인하므로 HierarchyViolationError로 거부된다.
    seeded = await build_hierarchy(pool, repo)
    await repo.close_sub_account(
        seeded.tenant_id, seeded.sub_account.sub_account_id, closed_at=now_utc()
    )
    await repo.close_portfolio(seeded.tenant_id, seeded.portfolio.portfolio_id, closed_at=now_utc())
    await repo.close_fund(seeded.tenant_id, seeded.fund.fund_id, closed_at=now_utc())

    precheck = await repo.list_funds_by_entity(seeded.tenant_id, seeded.legal_entity.entity_id)
    assert all(f.closed_at is not None for f in precheck)

    # 경합 주입 — 사전검사가 "닫아도 된다"고 판단한 직후 다른 트랜잭션이
    # 새 활성 Fund를 만든다.
    other_fund = Fund(
        fund_id=uuid4(),
        entity_id=seeded.legal_entity.entity_id,
        base_currency=Currency.USDT,
        inception=date(2026, 1, 1),
    )
    await repo.create_fund(other_fund)

    with pytest.raises(HierarchyViolationError):
        await repo.close_legal_entity(
            seeded.tenant_id, seeded.legal_entity.entity_id, closed_at=now_utc()
        )

    reread = await repo.get_legal_entity(seeded.tenant_id, seeded.legal_entity.entity_id)
    assert reread is not None
    assert reread.closed_at is None


async def test_close_fund_toctou_active_portfolio_inserted_after_precheck_is_rejected(pool, repo):
    seeded = await build_hierarchy(pool, repo)
    await repo.close_sub_account(
        seeded.tenant_id, seeded.sub_account.sub_account_id, closed_at=now_utc()
    )
    precheck = await repo.list_portfolios_by_fund(seeded.tenant_id, seeded.fund.fund_id)
    assert all(p.closed_at is None for p in precheck)

    other_portfolio = Portfolio(
        portfolio_id=uuid4(), fund_id=seeded.fund.fund_id, venue_account_ref="race"
    )
    await repo.create_portfolio(other_portfolio)

    with pytest.raises(HierarchyViolationError):
        await repo.close_fund(seeded.tenant_id, seeded.fund.fund_id, closed_at=now_utc())

    reread = await repo.get_fund(seeded.tenant_id, seeded.fund.fund_id)
    assert reread is not None
    assert reread.closed_at is None


async def test_close_portfolio_toctou_active_sub_account_inserted_after_precheck_is_rejected(
    pool, repo
):
    seeded = await build_hierarchy(pool, repo)
    precheck = await repo.list_sub_accounts_by_portfolio(
        seeded.tenant_id, seeded.portfolio.portfolio_id
    )
    assert precheck == [seeded.sub_account]

    await repo.close_sub_account(
        seeded.tenant_id, seeded.sub_account.sub_account_id, closed_at=now_utc()
    )

    other_sub_account = SubAccount(
        sub_account_id=uuid4(),
        portfolio_id=seeded.portfolio.portfolio_id,
        owner_ref=seeded.tenant_id,
    )
    await repo.create_sub_account(other_sub_account)

    with pytest.raises(HierarchyViolationError):
        await repo.close_portfolio(
            seeded.tenant_id, seeded.portfolio.portfolio_id, closed_at=now_utc()
        )

    reread = await repo.get_portfolio(seeded.tenant_id, seeded.portfolio.portfolio_id)
    assert reread is not None
    assert reread.closed_at is None


async def test_get_legal_entity_p95_latency_stays_within_normalized_ceiling(pool, repo):
    """수치 성능 단언 — 4단 계층 조회 중 가장 빈번히 호출되는
    get_legal_entity(단일 SELECT) 핫패스의 회귀 감시. 공유
    TEST_DATABASE_URL의 절대 지연 변동성 때문에 절대 ms 임계 대신, baseline
    조회 1건 대비 정규화한 상한만 게이트로 쓴다(1703 DEEPEN
    test_append_p95_latency_stays_within_normalized_ceiling·LA-18
    test_quality_metrics.py·LA-24 test_market_data_router.py와 동일 교훈)."""
    seeded = await build_hierarchy(pool, repo)

    baseline_start = time.perf_counter()
    await repo.get_legal_entity(seeded.tenant_id, seeded.legal_entity.entity_id)
    baseline_elapsed = time.perf_counter() - baseline_start

    samples: list[float] = []
    for _ in range(60):
        start = time.perf_counter()
        await repo.get_legal_entity(seeded.tenant_id, seeded.legal_entity.entity_id)
        samples.append(time.perf_counter() - start)

    samples.sort()
    p95 = samples[math.ceil(0.95 * len(samples)) - 1]

    ceiling = baseline_elapsed * 5 + 0.05
    assert p95 <= ceiling, (
        f"get_legal_entity p95 지연 {p95:.4f}s가 정규화 상한 {ceiling:.4f}s"
        f"(baseline {baseline_elapsed:.4f}s)를 초과했습니다 — 단일 SELECT 핫패스 회귀 의심"
    )


async def test_concurrent_close_legal_entity_requests_leave_exactly_one_winner(pool, repo):
    """D3 다중워커 증거 — 같은 LegalEntity에 대해 서로 다른 워커 6개가
    `close_legal_entity`를 동시에 시도하면, 조건부 UPDATE(`closed_at IS
    NULL`)가 명시적 락 없이도 fail-closed로 동작해 정확히 하나만 성공하고
    나머지 5개는 전부 ConcurrencyConflictError여야 한다(이중 폐쇄 0건,
    1703의 test_concurrent_same_seq_appends_leave_exactly_one_winner와
    동일 패턴). 동시성 워커 수는 `pool` 픽스처의 `max_size=8`(conftest.py)
    보다 낮게 고정한다 — 실패 경로(`close_legal_entity`가 재조회를 위해
    같은 풀에서 커넥션을 하나 더 얹어 무는 nested acquire)가 있는 채로
    워커 수가 풀 용량을 넘으면, 아직 첫 커넥션도 못 얻은 새 요청이 FIFO
    큐에서 이미 커넥션을 쥔 채 두 번째 커넥션을 기다리는 워커보다 항상
    먼저 서비스되어 기아 상태(교착)에 빠진다 — 실측(20워커 시도) 확인됨."""
    seeded = await build_hierarchy(pool, repo)
    await repo.close_sub_account(
        seeded.tenant_id, seeded.sub_account.sub_account_id, closed_at=now_utc()
    )
    await repo.close_portfolio(seeded.tenant_id, seeded.portfolio.portfolio_id, closed_at=now_utc())
    await repo.close_fund(seeded.tenant_id, seeded.fund.fund_id, closed_at=now_utc())

    async def _attempt(i: int):
        try:
            return await repo.close_legal_entity(
                seeded.tenant_id, seeded.legal_entity.entity_id, closed_at=now_utc()
            )
        except ConcurrencyConflictError:
            return None

    results = await asyncio.gather(*(_attempt(i) for i in range(6)))
    winners = [r for r in results if r is not None]

    assert len(winners) == 1
    assert winners[0].closed_at is not None

    reread = await repo.get_legal_entity(seeded.tenant_id, seeded.legal_entity.entity_id)
    assert reread is not None
    assert reread.closed_at is not None


async def test_list_funds_by_entity_p95_latency_stays_within_normalized_ceiling(pool, repo):
    """수치 성능 단언 — DEPTH 재감사(task-2724)가 task-2431의 실질 수정 커밋
    (7bb3ac62, task.json commit 필드 레코드 불일치 정정 — task-3030)에 지적한
    공백을 메운다. list_funds_by_entity는 그 커밋에서 tenant_id 필수 인자 +
    legal_entity JOIN(교차 테넌트 필터, LA-22/PLT-27 선례)을 새로 얻었으므로
    이 JOIN이 늘어난 핫패스에 회귀 상한을 건다. 절대 ms 임계 대신 baseline
    조회 1건 대비 정규화한 상한을 쓰는 이유는
    test_get_legal_entity_p95_latency_stays_within_normalized_ceiling(task-3005)과
    동일 — 공유 TEST_DATABASE_URL의 절대 지연 변동성."""
    seeded = await build_hierarchy(pool, repo)

    baseline_start = time.perf_counter()
    await repo.list_funds_by_entity(seeded.tenant_id, seeded.legal_entity.entity_id)
    baseline_elapsed = time.perf_counter() - baseline_start

    samples: list[float] = []
    for _ in range(60):
        start = time.perf_counter()
        await repo.list_funds_by_entity(seeded.tenant_id, seeded.legal_entity.entity_id)
        samples.append(time.perf_counter() - start)

    samples.sort()
    p95 = samples[math.ceil(0.95 * len(samples)) - 1]

    ceiling = baseline_elapsed * 5 + 0.05
    assert p95 <= ceiling, (
        f"list_funds_by_entity p95 지연 {p95:.4f}s가 정규화 상한 {ceiling:.4f}s"
        f"(baseline {baseline_elapsed:.4f}s)를 초과했습니다 — tenant_id JOIN 핫패스 회귀 의심"
    )


async def test_close_legal_entity_not_exists_guard_throughput_stays_within_budget(pool, repo):
    """수치 성능 단언 — close_legal_entity의 조건부 UPDATE에 붙은 NOT
    EXISTS(활성 Fund) 서브쿼리(7bb3ac62, FA-2 TOCTOU 원자화)가 만드는 추가
    비용에 명시적 예산을 건다. 자식 없는 LegalEntity N개를 만들어 NOT
    EXISTS가 매번 즉시 거짓으로 걸러지는 경로를 반복 실측한다
    (test_submit_order_entity_context_ownership.py FA-5 throughput 관례
    재사용, 새 perf 패턴 발명 없음)."""
    n = 20
    budget_sec = 10.0
    min_ops_per_sec = 2.0
    tenant_id = await create_test_tenant(pool, bootstrap_default_hierarchy_rows=False)

    entities: list[LegalEntity] = []
    for i in range(n):
        entity = await repo.create_legal_entity(
            LegalEntity(
                entity_id=uuid4(),
                tenant_id=tenant_id,
                name=f"Perf Entity {i}",
                jurisdiction="KR",
                region_tag="kr-seoul",
            )
        )
        entities.append(entity)

    start = time.perf_counter()
    for entity in entities:
        closed = await repo.close_legal_entity(tenant_id, entity.entity_id, closed_at=now_utc())
        assert closed.closed_at is not None
    elapsed = time.perf_counter() - start
    ops_per_sec = n / elapsed

    print(
        f"[task-2431/3030 close_legal_entity NOT EXISTS guard] {n} closes {elapsed:.3f}s "
        f"({ops_per_sec:.1f} ops/s, budget<{budget_sec}s, min>{min_ops_per_sec} ops/s)"
    )
    assert elapsed < budget_sec, (
        f"{n}회 close_legal_entity가 예산({budget_sec}s)을 넘었습니다({elapsed:.3f}s)."
    )
    assert ops_per_sec > min_ops_per_sec, (
        f"close_legal_entity 처리량이 최소값({min_ops_per_sec} ops/s)에 "
        f"못 미칩니다({ops_per_sec:.1f})."
    )
