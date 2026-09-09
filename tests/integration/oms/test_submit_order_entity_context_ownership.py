"""task-1925(FA-5 리뷰 REJECT 후속) `application/submit_order.py` 실소유권
재검증 통합테스트 — 실 TEST_DATABASE_URL.

리뷰어가 실DB로 재현한 결함: `entity_context.tenant_id`만 `cmd.scope.tenant_id`와
비교하고 fund_id 등의 실소유권은 확인하지 않아, 같은 tenant_id에 타 테넌트
fund_id를 담은 위조 `EntityContext`를 넘기면 orders 1행이 그 fund_id로 실제
생성됐다. `verify_entity_context()`(resolve_context.py) 배선 이후 DoD:
(a) 위조 EntityContext(같은 tenant_id + 타 테넌트 fund_id) 거부 + orders 0행,
(b) 폐쇄된 fund를 담은 컨텍스트도 같은 예외로 거부,
(c) 정상 경로에서 orders.fund_id/portfolio_id == entity_context 산출값.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from src.data.models.base import AssetClass
from src.data.models.trading import OrderSide, OrderType
from src.foundation.entities.adapters.postgres_repository import PostgresEntityRepository
from src.foundation.entities.application.resolve_context import EntityContextResolutionError
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
        "BTC/USDT", "bitget", "BTCUSDT",
        tick=Decimal("0.1"), lot=Decimal("0.0001"), min_notional=Decimal("5"), quote_ccy="USDT",
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
            strategy_id, user_id, json.dumps({}),
        )
        row = await conn.fetchrow(
            """
            INSERT INTO strategy_executions
                (strategy_id, strategy_version, user_id, exchange, mode,
                 allocated_capital, currency, status)
            VALUES ($1, '1.0.0', $2, 'bitget', 'PAPER', 100, 'USDT', 'RUNNING')
            RETURNING id
            """,
            strategy_id, user_id,
        )
    return row["id"]


def _command(user_id: uuid.UUID, execution_id: int) -> SubmitOrderCommand:
    scope = OrderIdempotencyScope(
        tenant_id=user_id, account_ref="acct-1", provider="bitget", strategy_id="s1",
        strategy_version="1.0.0", execution_id=execution_id, intent_seq=1,
        window_start=datetime.now(timezone.utc),
    )
    return SubmitOrderCommand(
        command_id=uuid.uuid4(), trace_id=uuid.uuid4(), scope=scope, symbol="BTC/USDT",
        side=OrderSide.BUY, order_type=OrderType.MARKET, quantity=Decimal("0.01"),
        asset_class=AssetClass.CRYPTO, actor_subject_id=user_id,
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
            cmd, pool=pool, profile=_profile(), registry=_registry(),
            pre_submit_gate=_allow_gate, entity_context=forged, entity_repo=entity_repo,
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
            cmd, pool=pool, profile=_profile(), registry=_registry(),
            pre_submit_gate=_allow_gate, entity_context=context, entity_repo=entity_repo,
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
        cmd, pool=pool, profile=_profile(), registry=_registry(),
        pre_submit_gate=_allow_gate, entity_context=context, entity_repo=entity_repo,
    )

    async with pool.acquire() as conn:
        order_row = await conn.fetchrow(
            "SELECT fund_id, portfolio_id FROM orders WHERE order_id = $1", result.order_id
        )
    assert order_row["fund_id"] == context.fund_id
    assert order_row["portfolio_id"] == context.portfolio_id
