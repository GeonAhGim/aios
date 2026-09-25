"""FA-8 `allocate_order_fills` 통합테스트 — 실 DB(TEST_DATABASE_URL) 대상.

DoD(§9 FA-8): 배분 합계 == 체결(FA-A3, 도메인이 이미 강제 — 여기서는 결과가
그대로 반영되는지만 확인), 원장 연결(sub_account별 `MANUAL_ADJUSTMENT` 분개가
실제로 잔액을 바꾸고, 같은 order_id로 재호출해도 REPLAY라 중복 반영되지
않음).

DEPTH 감사(task-2724, docs/audit/DEPTH_FA.md)가 원 task-1796의 마지막 팔로업
커밋(f158ebb, 독스트링 압축뿐)만 보고 D0로 판정한 공백(negative/실패주입/
성능단언/게이트재현/D3증명)을 이 DEEPEN(task-3012)에서 메운다 — closed
sub_account 거부(negative 3번째), 루프 중간 인프라 장애 시 트랜잭션 전체
롤백(실패주입+게이트재현, 부분 반영 없음을 증명), 동일 order_id 동시 호출이
정확히 1건만 비-REPLAY로 반영(D3, LC-9 advisory lock 직렬화 증거), append
지연 정규화 상한(성능단언, LA-18/LA-24/task-3004와 동일 교훈으로 절대 ms
대신 baseline 대비 배수 상한을 쓴다)."""

from __future__ import annotations

import asyncio
import math
import time
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from src.foundation.allocation.application.allocate_fills import (
    AllocationTargetError,
    NoFillsError,
    allocate_order_fills,
)
from src.foundation.allocation.domain.policy import AllocationPolicy, WeightTarget
from src.foundation.entities.adapters.postgres_repository import PostgresEntityRepository
from src.foundation.entities.contracts.v1 import SubAccount
from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.ledger.adapters.postgres_balance_repository import PostgresBalanceRepository
from src.foundation.ledger.adapters.postgres_journal_repository import PostgresJournalRepository
from tests.integration.foundation.allocation.conftest import (
    create_order_with_fills,
    create_test_user,
    wallet_available_balance,
)
from tests.integration.foundation.entities.conftest import build_hierarchy


def _clock() -> datetime:
    return datetime.now(timezone.utc)


async def _ports(pool):
    return (
        PostgresEntityRepository(pool),
        PostgresJournalRepository(pool),
        PostgresBalanceRepository(pool),
        PostgresAuditEventRepository(pool),
    )


async def test_allocate_order_fills_splits_pro_rata_and_posts_ledger(pool):
    entities, journal, balances, audit = await _ports(pool)
    hierarchy = await build_hierarchy(pool, entities)
    owner_b = await create_test_user(pool)
    sub_account_b = await entities.create_sub_account(
        SubAccount(
            sub_account_id=uuid4(),
            portfolio_id=hierarchy.portfolio.portfolio_id,
            owner_ref=owner_b,
        )
    )

    order_id = await create_order_with_fills(
        pool,
        user_id=hierarchy.tenant_id,
        fund_id=hierarchy.fund.fund_id,
        portfolio_id=hierarchy.portfolio.portfolio_id,
        side="SELL",
        fills=[(Decimal("100"), Decimal("150.00")), (Decimal("100"), Decimal("152.00"))],
    )

    async with pool.acquire() as conn, conn.transaction():
        results = await allocate_order_fills(
            conn,
            tenant_id=hierarchy.tenant_id,
            order_id=order_id,
            policy=AllocationPolicy.PRO_RATA,
            weight_targets=(
                WeightTarget(
                    sub_account_id=hierarchy.sub_account.sub_account_id, weight=Decimal("1")
                ),
                WeightTarget(sub_account_id=sub_account_b.sub_account_id, weight=Decimal("1")),
            ),
            quantum=Decimal("1"),
            price_quantum=Decimal("0.01"),
            entities=entities,
            journal=journal,
            balances=balances,
            audit=audit,
            clock=_clock,
            trace_id=uuid4(),
        )

    assert len(results) == 2
    assert sum(r.quantity for r in results) == Decimal("200")
    for r in results:
        assert r.quantity == Decimal("100")
        assert r.average_price == Decimal("151.00")
        assert r.replayed is False

    assert await wallet_available_balance(pool, hierarchy.tenant_id) == Decimal("15100.00")
    assert await wallet_available_balance(pool, owner_b) == Decimal("15100.00")

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT sub_account_id, quantity, average_price FROM fill_allocation "
            "WHERE order_id = $1 ORDER BY sub_account_id",
            order_id,
        )
    assert len(rows) == 2


async def test_allocate_order_fills_same_order_twice_does_not_double_post(pool):
    entities, journal, balances, audit = await _ports(pool)
    hierarchy = await build_hierarchy(pool, entities)
    order_id = await create_order_with_fills(
        pool,
        user_id=hierarchy.tenant_id,
        fund_id=hierarchy.fund.fund_id,
        portfolio_id=hierarchy.portfolio.portfolio_id,
        side="SELL",
        fills=[(Decimal("10"), Decimal("100.00"))],
    )
    targets = (
        WeightTarget(sub_account_id=hierarchy.sub_account.sub_account_id, weight=Decimal("1")),
    )

    async def _run():
        async with pool.acquire() as conn, conn.transaction():
            return await allocate_order_fills(
                conn,
                tenant_id=hierarchy.tenant_id,
                order_id=order_id,
                policy=AllocationPolicy.PRO_RATA,
                weight_targets=targets,
                quantum=Decimal("1"),
                price_quantum=Decimal("0.01"),
                entities=entities,
                journal=journal,
                balances=balances,
                audit=audit,
                clock=_clock,
                trace_id=uuid4(),
            )

    first = await _run()
    second = await _run()

    assert first[0].replayed is False
    assert second[0].replayed is True
    assert await wallet_available_balance(pool, hierarchy.tenant_id) == Decimal("1000.00")

    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM fill_allocation WHERE order_id = $1", order_id
        )
    assert count == 1


async def test_allocate_order_fills_no_fills_rejected(pool):
    entities, journal, balances, audit = await _ports(pool)
    hierarchy = await build_hierarchy(pool, entities)
    order_id = await create_order_with_fills(
        pool,
        user_id=hierarchy.tenant_id,
        fund_id=hierarchy.fund.fund_id,
        portfolio_id=hierarchy.portfolio.portfolio_id,
        side="SELL",
        fills=[],
    )

    with pytest.raises(NoFillsError):
        async with pool.acquire() as conn, conn.transaction():
            await allocate_order_fills(
                conn,
                tenant_id=hierarchy.tenant_id,
                order_id=order_id,
                policy=AllocationPolicy.PRO_RATA,
                weight_targets=(
                    WeightTarget(
                        sub_account_id=hierarchy.sub_account.sub_account_id, weight=Decimal("1")
                    ),
                ),
                quantum=Decimal("1"),
                price_quantum=Decimal("0.01"),
                entities=entities,
                journal=journal,
                balances=balances,
                audit=audit,
                clock=_clock,
                trace_id=uuid4(),
            )


async def test_allocate_order_fills_unknown_sub_account_rejected(pool):
    entities, journal, balances, audit = await _ports(pool)
    hierarchy = await build_hierarchy(pool, entities)
    order_id = await create_order_with_fills(
        pool,
        user_id=hierarchy.tenant_id,
        fund_id=hierarchy.fund.fund_id,
        portfolio_id=hierarchy.portfolio.portfolio_id,
        side="SELL",
        fills=[(Decimal("10"), Decimal("100.00"))],
    )

    with pytest.raises(AllocationTargetError):
        async with pool.acquire() as conn, conn.transaction():
            await allocate_order_fills(
                conn,
                tenant_id=hierarchy.tenant_id,
                order_id=order_id,
                policy=AllocationPolicy.PRO_RATA,
                weight_targets=(WeightTarget(sub_account_id=uuid4(), weight=Decimal("1")),),
                quantum=Decimal("1"),
                price_quantum=Decimal("0.01"),
                entities=entities,
                journal=journal,
                balances=balances,
                audit=audit,
                clock=_clock,
                trace_id=uuid4(),
            )


async def test_allocate_order_fills_closed_sub_account_rejected(pool):
    entities, journal, balances, audit = await _ports(pool)
    hierarchy = await build_hierarchy(pool, entities)
    await entities.close_sub_account(
        hierarchy.tenant_id, hierarchy.sub_account.sub_account_id, closed_at=_clock()
    )
    order_id = await create_order_with_fills(
        pool,
        user_id=hierarchy.tenant_id,
        fund_id=hierarchy.fund.fund_id,
        portfolio_id=hierarchy.portfolio.portfolio_id,
        side="SELL",
        fills=[(Decimal("10"), Decimal("100.00"))],
    )

    with pytest.raises(AllocationTargetError):
        async with pool.acquire() as conn, conn.transaction():
            await allocate_order_fills(
                conn,
                tenant_id=hierarchy.tenant_id,
                order_id=order_id,
                policy=AllocationPolicy.PRO_RATA,
                weight_targets=(
                    WeightTarget(
                        sub_account_id=hierarchy.sub_account.sub_account_id, weight=Decimal("1")
                    ),
                ),
                quantum=Decimal("1"),
                price_quantum=Decimal("0.01"),
                entities=entities,
                journal=journal,
                balances=balances,
                audit=audit,
                clock=_clock,
                trace_id=uuid4(),
            )


async def test_allocate_order_fills_mid_loop_failure_rolls_back_everything(pool, monkeypatch):
    """실패주입+게이트재현 — 여러 sub_account 중 두 번째 대상 해석에서
    인프라 장애(RuntimeError)를 주입한다. `allocate_order_fills`는 자신의
    트랜잭션을 열지 않으므로(호출자 `conn.transaction()`이 경계), 첫 번째
    대상이 이미 분개+`fill_allocation` INSERT까지 마친 뒤라도 예외가
    전파되면 전체가 롤백되어 부분 반영이 남지 않아야 한다."""
    entities, journal, balances, audit = await _ports(pool)
    hierarchy = await build_hierarchy(pool, entities)
    owner_b = await create_test_user(pool)
    sub_account_b = await entities.create_sub_account(
        SubAccount(
            sub_account_id=uuid4(),
            portfolio_id=hierarchy.portfolio.portfolio_id,
            owner_ref=owner_b,
        )
    )
    order_id = await create_order_with_fills(
        pool,
        user_id=hierarchy.tenant_id,
        fund_id=hierarchy.fund.fund_id,
        portfolio_id=hierarchy.portfolio.portfolio_id,
        side="SELL",
        fills=[(Decimal("10"), Decimal("100.00"))],
    )

    real_get_sub_account = entities.get_sub_account
    calls = {"n": 0}

    async def _flaky_get_sub_account(tenant_id, sub_account_id):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("simulated infra failure mid-allocation")
        return await real_get_sub_account(tenant_id, sub_account_id)

    monkeypatch.setattr(entities, "get_sub_account", _flaky_get_sub_account)

    with pytest.raises(RuntimeError, match="simulated infra failure"):
        async with pool.acquire() as conn, conn.transaction():
            await allocate_order_fills(
                conn,
                tenant_id=hierarchy.tenant_id,
                order_id=order_id,
                policy=AllocationPolicy.PRO_RATA,
                weight_targets=(
                    WeightTarget(
                        sub_account_id=hierarchy.sub_account.sub_account_id, weight=Decimal("1")
                    ),
                    WeightTarget(sub_account_id=sub_account_b.sub_account_id, weight=Decimal("1")),
                ),
                quantum=Decimal("1"),
                price_quantum=Decimal("0.01"),
                entities=entities,
                journal=journal,
                balances=balances,
                audit=audit,
                clock=_clock,
                trace_id=uuid4(),
            )

    assert calls["n"] == 2
    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM fill_allocation WHERE order_id = $1", order_id
        )
    assert count == 0
    assert await wallet_available_balance(pool, hierarchy.tenant_id) == Decimal("0")


async def test_allocate_order_fills_concurrent_same_order_produces_exactly_one_non_replayed(
    pool,
):
    """D3증명 — 동일 order_id를 동시에 여러 번 호출해도(각자 독립 conn+
    트랜잭션) LC-9 `post_entry`의 멱등 lookup이 advisory lock으로 직렬화돼
    정확히 1건만 비-REPLAY로 반영되고, 나머지는 REPLAY로 흡수되어야 한다
    (post_entry.py 모듈 독스트링 참고 — `journal.append`의 advisory lock
    판정만이 잔액 반영을 건너뛸 유일한 근거)."""
    entities, journal, balances, audit = await _ports(pool)
    hierarchy = await build_hierarchy(pool, entities)
    order_id = await create_order_with_fills(
        pool,
        user_id=hierarchy.tenant_id,
        fund_id=hierarchy.fund.fund_id,
        portfolio_id=hierarchy.portfolio.portfolio_id,
        side="SELL",
        fills=[(Decimal("10"), Decimal("100.00"))],
    )
    targets = (
        WeightTarget(sub_account_id=hierarchy.sub_account.sub_account_id, weight=Decimal("1")),
    )

    async def _run():
        async with pool.acquire() as conn, conn.transaction():
            return await allocate_order_fills(
                conn,
                tenant_id=hierarchy.tenant_id,
                order_id=order_id,
                policy=AllocationPolicy.PRO_RATA,
                weight_targets=targets,
                quantum=Decimal("1"),
                price_quantum=Decimal("0.01"),
                entities=entities,
                journal=journal,
                balances=balances,
                audit=audit,
                clock=_clock,
                trace_id=uuid4(),
            )

    all_results = await asyncio.gather(*[_run() for _ in range(10)])
    replayed_flags = [results[0].replayed for results in all_results]

    assert replayed_flags.count(False) == 1
    assert replayed_flags.count(True) == 9
    assert await wallet_available_balance(pool, hierarchy.tenant_id) == Decimal("1000.00")

    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM fill_allocation WHERE order_id = $1", order_id
        )
    assert count == 1


@pytest.mark.perf
async def test_allocate_order_fills_latency_stays_within_normalized_ceiling(pool):
    """수치 성능 단언 — 공유 TEST_DATABASE_URL의 절대 지연 변동성 때문에
    절대 ms 임계 대신, 가벼운 baseline 호출 1건 대비 정규화한 상한만
    게이트로 쓴다(LA-18 test_quality_metrics.py·LA-24
    test_market_data_router.py·task-3004 test_append.py와 동일 교훈)."""
    entities, journal, balances, audit = await _ports(pool)
    hierarchy = await build_hierarchy(pool, entities)
    targets = (
        WeightTarget(sub_account_id=hierarchy.sub_account.sub_account_id, weight=Decimal("1")),
    )

    async def _run_once() -> float:
        order_id = await create_order_with_fills(
            pool,
            user_id=hierarchy.tenant_id,
            fund_id=hierarchy.fund.fund_id,
            portfolio_id=hierarchy.portfolio.portfolio_id,
            side="SELL",
            fills=[(Decimal("1"), Decimal("100.00"))],
        )
        start = time.perf_counter()
        async with pool.acquire() as conn, conn.transaction():
            await allocate_order_fills(
                conn,
                tenant_id=hierarchy.tenant_id,
                order_id=order_id,
                policy=AllocationPolicy.PRO_RATA,
                weight_targets=targets,
                quantum=Decimal("1"),
                price_quantum=Decimal("0.01"),
                entities=entities,
                journal=journal,
                balances=balances,
                audit=audit,
                clock=_clock,
                trace_id=uuid4(),
            )
        return time.perf_counter() - start

    baseline_elapsed = await _run_once()

    samples = [await _run_once() for _ in range(20)]
    samples.sort()
    p95 = samples[math.ceil(0.95 * len(samples)) - 1]

    ceiling = baseline_elapsed * 5 + 0.05
    assert p95 <= ceiling, (
        f"allocate_order_fills p95 지연 {p95:.4f}s가 정규화 상한 {ceiling:.4f}s"
        f"(baseline {baseline_elapsed:.4f}s)를 초과했습니다 -- 회귀 의심"
    )
