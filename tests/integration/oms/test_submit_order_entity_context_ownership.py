"""task-1925(FA-5 리뷰 REJECT 후속) `application/submit_order.py` 실소유권
재검증 통합테스트 — 실 TEST_DATABASE_URL.

리뷰어가 실DB로 재현한 결함: `entity_context.tenant_id`만 `cmd.scope.tenant_id`와
비교하고 fund_id 등의 실소유권은 확인하지 않아, 같은 tenant_id에 타 테넌트
fund_id를 담은 위조 `EntityContext`를 넘기면 orders 1행이 그 fund_id로 실제
생성됐다. `verify_entity_context()`(resolve_context.py) 배선 이후 DoD:
(a) 위조 EntityContext(같은 tenant_id + 타 테넌트 fund_id) 거부 + orders 0행,
(b) 폐쇄된 fund를 담은 컨텍스트도 같은 예외로 거부,
(c) 정상 경로에서 orders.fund_id/portfolio_id == entity_context 산출값.

DEPTH_FA(task-2724)가 원 task-1925를 D1로 재판정한 근거 중 "수치 성능
단언 없음"을 메우는 두 perf 테스트(`@pytest.mark.perf`)를 이 파일 끝에
추가한다 — `verify_entity_context()`가 `submit_order()` INSERT 직전마다
4단 계층을 재조회하는 추가 왕복을 만들었으므로(위 docstring 결함 수정),
그 오버헤드에 명시적 예산을 건다(research_items RD-4 test_research_
repository.py의 latency/throughput 관례 재사용, 새 perf 패턴 발명 없음).
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from src.data.models.base import AssetClass
from src.data.models.trading import OrderSide, OrderType
from src.foundation.entities.adapters.postgres_repository import PostgresEntityRepository
from src.foundation.entities.application.resolve_context import (
    EntityContextResolutionError,
    verify_entity_context,
)
from src.services.oms.application.submit_order import submit_order
from src.services.oms.contracts.v1_commands import OrderIdempotencyScope, SubmitOrderCommand
from src.services.oms.domain.symbol_registry import SymbolRegistry
from src.services.oms.domain.venue_profile import TimeoutBudget, VenueCapabilityProfile
from src.services.order_service.gate import GateDecision, GateOutcome, OrderContext
from tests.integration.oms.conftest import create_test_tenant, seed_entity_context


def _profile(**overrides: object) -> VenueCapabilityProfile:
    defaults: dict[str, object] = {
        "venue": "bitget",
        "asset_classes": [AssetClass.CRYPTO],
        "order_types": {OrderType.MARKET, OrderType.LIMIT},
        "time_in_force": {"GTC", "IOC"},
        "supports_client_order_id": True,
        "client_order_id_max_len": 40,
        "client_order_id_charset": "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
        "id_policy": "STABLE",
        "supports_modify": True,
        "supports_cancel": "YES",
        "supports_ws_orders": True,
        "supports_batch": False,
        "price_tick": {},
        "qty_lot": {},
        "min_notional": {},
        "rate_limits": {},
        "submit_timeout": TimeoutBudget(),
        "query_timeout": TimeoutBudget(),
        "market_hours": None,
        "max_open_orders_per_symbol": 20,
        "verified": "DOC_ONLY",
    }
    defaults.update(overrides)
    return VenueCapabilityProfile.model_validate(defaults)


def _registry() -> SymbolRegistry:
    reg = SymbolRegistry()
    reg.register(
        "BTC/USDT",
        "bitget",
        "BTCUSDT",
        tick=Decimal("0.1"),
        lot=Decimal("0.0001"),
        min_notional=Decimal("5"),
        quote_ccy="USDT",
    )
    return reg


async def _create_running_execution(pool, user_id: uuid.UUID) -> int:
    strategy_id = f"oms-ownership-test-{uuid.uuid4().hex[:8]}"
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO strategies
                (strategy_id, version, owner_user_id, target_asset, market, exchange,
                 fsm_definition, author_agent, lifecycle_status)
            VALUES ($1, '1.0.0', $2, 'BTC/USDT', 'crypto', 'bitget', $3::jsonb,
                    'test-author', 'APPROVED')
            """,
            strategy_id,
            user_id,
            json.dumps({}),
        )
        row = await conn.fetchrow(
            """
            INSERT INTO strategy_executions
                (strategy_id, strategy_version, user_id, exchange, mode,
                 allocated_capital, currency, status)
            VALUES ($1, '1.0.0', $2, 'bitget', 'PAPER', 100, 'USDT', 'RUNNING')
            RETURNING id
            """,
            strategy_id,
            user_id,
        )
    return row["id"]


def _command(user_id: uuid.UUID, execution_id: int, intent_seq: int = 1) -> SubmitOrderCommand:
    scope = OrderIdempotencyScope(
        tenant_id=user_id,
        account_ref="acct-1",
        provider="bitget",
        strategy_id="s1",
        strategy_version="1.0.0",
        execution_id=execution_id,
        intent_seq=intent_seq,
        window_start=datetime.now(timezone.utc),
    )
    return SubmitOrderCommand(
        command_id=uuid.uuid4(),
        trace_id=uuid.uuid4(),
        scope=scope,
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("0.01"),
        asset_class=AssetClass.CRYPTO,
        actor_subject_id=user_id,
        issued_at=datetime.now(timezone.utc),
    )


async def _allow_gate(context: OrderContext) -> GateDecision:
    return GateDecision(
        outcome=GateOutcome.ALLOW, decision_id=uuid.uuid4(), compliance_decision_id=uuid.uuid4()
    )


async def test_submit_order_rejects_forged_entity_context_cross_tenant_fund(pool):
    """DoD(a) — 같은 tenant_id에 타 테넌트 소유 fund_id를 담은 위조
    EntityContext는 거부되고, 그 fund_id로 orders 행이 생기지 않는다. 이
    단언이 `verify_entity_context()` 호출 배선을 지운 회귀에서 FAIL한다."""
    tenant_a = await create_test_tenant(pool)
    execution_id = await _create_running_execution(pool, tenant_a)
    cmd = _command(tenant_a, execution_id)
    context_a = await seed_entity_context(pool, tenant_a)

    tenant_b = await create_test_tenant(pool)
    context_b = await seed_entity_context(pool, tenant_b)

    forged = context_a.model_copy(update={"fund_id": context_b.fund_id})
    entity_repo = PostgresEntityRepository(pool)

    with pytest.raises(EntityContextResolutionError):
        await submit_order(
            cmd,
            pool=pool,
            profile=_profile(),
            registry=_registry(),
            pre_submit_gate=_allow_gate,
            entity_context=forged,
            entity_repo=entity_repo,
        )

    async with pool.acquire() as conn:
        order_count = await conn.fetchval(
            "SELECT count(*) FROM orders WHERE fund_id = $1", context_b.fund_id
        )
    assert order_count == 0


async def test_submit_order_rejects_closed_fund_in_entity_context(pool):
    """DoD(b) — entity_context가 가리키는 fund가 폐쇄됐으면(자기 테넌트
    소유라도) 같은 예외로 거부된다."""
    tenant_id = await create_test_tenant(pool)
    execution_id = await _create_running_execution(pool, tenant_id)
    cmd = _command(tenant_id, execution_id)
    context = await seed_entity_context(pool, tenant_id)
    entity_repo = PostgresEntityRepository(pool)
    # 상위 폐쇄는 활성 하위가 없어야 하므로(NOT EXISTS 절, FA-2) 최하위부터
    # 순서대로 닫는다.
    await entity_repo.close_sub_account(
        tenant_id, context.sub_account_id, closed_at=datetime.now(timezone.utc)
    )
    await entity_repo.close_portfolio(
        tenant_id, context.portfolio_id, closed_at=datetime.now(timezone.utc)
    )
    await entity_repo.close_fund(tenant_id, context.fund_id, closed_at=datetime.now(timezone.utc))

    with pytest.raises(EntityContextResolutionError):
        await submit_order(
            cmd,
            pool=pool,
            profile=_profile(),
            registry=_registry(),
            pre_submit_gate=_allow_gate,
            entity_context=context,
            entity_repo=entity_repo,
        )

    async with pool.acquire() as conn:
        order_count = await conn.fetchval(
            "SELECT count(*) FROM orders WHERE execution_id = $1", execution_id
        )
    assert order_count == 0


async def test_submit_order_persists_entity_context_fund_and_portfolio_ids(pool):
    """DoD(c) — 정상 경로에서 orders.fund_id/portfolio_id가 entity_context
    산출값과 정확히 같다."""
    tenant_id = await create_test_tenant(pool)
    execution_id = await _create_running_execution(pool, tenant_id)
    cmd = _command(tenant_id, execution_id)
    context = await seed_entity_context(pool, tenant_id)
    entity_repo = PostgresEntityRepository(pool)

    result = await submit_order(
        cmd,
        pool=pool,
        profile=_profile(),
        registry=_registry(),
        pre_submit_gate=_allow_gate,
        entity_context=context,
        entity_repo=entity_repo,
    )

    async with pool.acquire() as conn:
        order_row = await conn.fetchrow(
            "SELECT fund_id, portfolio_id FROM orders WHERE order_id = $1", result.order_id
        )
    assert order_row["fund_id"] == context.fund_id
    assert order_row["portfolio_id"] == context.portfolio_id


@pytest.mark.perf
async def test_verify_entity_context_latency_under_budget(
    pool,
) -> None:
    """verify_entity_context() 단일 호출 지연 예산: p95 < 50ms(ADR-2026-09-09-C 주문 제출→ACK p95).

    ADR-2026-09-09-C Decision 1: latency budgets — "주문 제출→ACK p95 50ms(paper)".
    verify_entity_context는 resolve_context.verify_entity_context() 한 호출이며,
    같은 함수가 order submit→ACK 파이프라인에서 호출되므로 동일 예산 적용.
    """
    budget_sec = 0.050  # p95 < 50ms
    n = 100
    tenant_id = await create_test_tenant(pool)
    context = await seed_entity_context(pool, tenant_id)
    entity_repo = PostgresEntityRepository(pool)
    latencies: list[float] = []
    for _ in range(n):
        t0 = time.perf_counter()
        await verify_entity_context(entity_repo, context)
        latencies.append((time.perf_counter() - t0) * 1000)  # ms
    latencies.sort()
    p95_idx = int(n * 0.95)
    p99_idx = int(n * 0.99)
    p95_ms = latencies[p95_idx]
    p99_ms = latencies[p99_idx]
    assert p95_ms < budget_sec * 1000, (
        f"verify_entity_context p95={p95_ms:.1f}ms > budget {budget_sec * 1000:.0f}ms (n={n})"
    )
    assert p99_ms < budget_sec * 2000, (
        f"verify_entity_context p99={p99_ms:.1f}ms > 2×budget {budget_sec * 2000:.0f}ms (n={n})"
    )

@pytest.mark.perf
async def test_submit_order_throughput_with_entity_context_verification(pool):
    """수치 성능 단언 — `entity_repo` 실소유권 재검증이 배선된 `submit_order()`
    경로의 처리량. 서로 다른 `intent_seq`로 스코프를 갈라 매 호출이 새 orders
    행을 만들게 하고(멱등 재사용 경로가 아니라 verify_entity_context를 포함한
    전체 INSERT 경로를 매번 실측), N회 순차 제출의 총 소요/처리량에 예산을
    건다(research_items RD-4 throughput 테스트와 동일 관례).

    ADR-2026-09-09-C Decision 1: latency budgets — "주문 제출→ACK p95 50ms(paper)".
    20 ops × 50ms = 1s 최소, DB 오버헤드 고려해 budget 5.0s, min_ops 5.0 ops/s.
    """
    n = 20
    budget_sec = 5.0
    min_ops_per_sec = 5.0
    tenant_id = await create_test_tenant(pool)
    execution_id = await _create_running_execution(pool, tenant_id)
    context = await seed_entity_context(pool, tenant_id)
    entity_repo = PostgresEntityRepository(pool)

    start = time.perf_counter()
    for seq in range(n):
        result = await submit_order(
            _command(tenant_id, execution_id, intent_seq=seq),
            pool=pool,
            profile=_profile(),
            registry=_registry(),
            pre_submit_gate=_allow_gate,
            entity_context=context,
            entity_repo=entity_repo,
        )
        assert result.order_id is not None
    elapsed = time.perf_counter() - start
    ops_per_sec = n / elapsed

    print(
        f"[task-1925 submit_order] {n} submits (entity_context verified each) {elapsed:.3f}s "
        f"({ops_per_sec:.1f} ops/s, budget<{budget_sec}s, min>{min_ops_per_sec} ops/s)"
    )
    assert elapsed < budget_sec, (
        f"{n}회 submit_order가 예산({budget_sec}s)을 넘었습니다({elapsed:.3f}s)."
    )
    assert ops_per_sec > min_ops_per_sec, (
        f"submit_order 처리량이 최소값({min_ops_per_sec} ops/s)에 못 미칩니다({ops_per_sec:.1f})."
    )
