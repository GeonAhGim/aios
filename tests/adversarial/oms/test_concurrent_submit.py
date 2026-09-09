"""L4-09 적대적 테스트 — `application/submit_order.py`.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §9 L4-09 DoD
"동시 50 submit → 1행(adversarial)". 절대 지연(sleep 시간) 단언은 하지
않는다 — 구조(행 수) 단언과 print만(TESTING.md 관례).
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import asyncpg
import pytest

from src.data.models.base import AssetClass
from src.data.models.trading import OrderSide, OrderType
from src.foundation.entities.adapters.postgres_repository import PostgresEntityRepository
from src.services.oms.application.submit_order import OrderSubmitDeniedError, submit_order
from src.services.oms.contracts.v1_commands import OrderIdempotencyScope, SubmitOrderCommand
from src.services.oms.domain.symbol_registry import SymbolRegistry
from src.services.oms.domain.venue_profile import TimeoutBudget, VenueCapabilityProfile
from src.services.order_service.gate import GateDecision, GateOutcome, OrderContext
from tests.integration.oms.conftest import create_test_tenant, seed_entity_context

_CONCURRENCY = 50


def _asyncpg_dsn() -> str:
    return os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    # I-01/50동시 시나리오는 승자 1명만 게이트를 위해 2번째 커넥션을 잠깐
    # 더 쥔다(submit_order.py 모듈 docstring) — 나머지 49는 EXISTING이라
    # 커넥션 1개만 쓴다. 여유있게 min/max를 잡아 풀 경합 자체가 결과를
    # 흔들지 않게 한다.
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=4, max_size=_CONCURRENCY + 10)
    yield p
    await p.close()


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
    return VenueCapabilityProfile(**defaults)  # type: ignore[arg-type]


def _registry() -> SymbolRegistry:
    reg = SymbolRegistry()
    reg.register(
        "BTC/USDT", "bitget", "BTCUSDT",
        tick=Decimal("0.1"), lot=Decimal("0.0001"), min_notional=Decimal("5"), quote_ccy="USDT",
    )
    return reg


async def _create_running_execution(pool: asyncpg.Pool, user_id: uuid.UUID) -> int:
    strategy_id = f"oms-adv-test-{uuid.uuid4().hex[:8]}"
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


async def _kill_switch_gate(context: OrderContext) -> GateDecision:
    return GateDecision(outcome=GateOutcome.DENY, reason_codes=("RISK_KILL_SWITCH_ACTIVE_GLOBAL",))


async def test_50_concurrent_same_intent_submits_produce_exactly_one_row(pool):
    """DoD — 같은 의도(scope_hash)로 50개 동시 submit → orders 1행·outbox 1행."""
    user_id = await create_test_tenant(pool)
    execution_id = await _create_running_execution(pool, user_id)
    cmd = _command(user_id, execution_id)
    entity_context = await seed_entity_context(pool, user_id)

    entity_repo = PostgresEntityRepository(pool)
    results = await asyncio.gather(
        *[
            submit_order(
                cmd, pool=pool, profile=_profile(), registry=_registry(),
                pre_submit_gate=_allow_gate, entity_context=entity_context,
                entity_repo=entity_repo,
            )
            for _ in range(_CONCURRENCY)
        ]
    )

    order_ids = {r.order_id for r in results}
    print(f"submit_order concurrent={_CONCURRENCY} distinct_order_ids={len(order_ids)}")
    assert len(order_ids) == 1
    async with pool.acquire() as conn:
        order_count = await conn.fetchval(
            "SELECT count(*) FROM orders WHERE execution_id = $1", execution_id
        )
        outbox_count = await conn.fetchval(
            "SELECT count(*) FROM order_command_outbox WHERE order_id = $1", order_ids.pop()
        )
    assert order_count == 1
    assert outbox_count == 1


async def test_gate_none_is_fail_closed_type_error(pool):
    """I-01 — `pre_submit_gate=None`을 명시적으로 넘기면 TypeError, 0행."""
    user_id = await create_test_tenant(pool)
    execution_id = await _create_running_execution(pool, user_id)
    cmd = _command(user_id, execution_id)
    entity_context = await seed_entity_context(pool, user_id)

    with pytest.raises(TypeError):
        await submit_order(
            cmd, pool=pool, profile=_profile(), registry=_registry(),
            pre_submit_gate=None,  # type: ignore[arg-type]
            entity_context=entity_context, entity_repo=PostgresEntityRepository(pool),
        )

    async with pool.acquire() as conn:
        order_count = await conn.fetchval(
            "SELECT count(*) FROM orders WHERE execution_id = $1", execution_id
        )
    assert order_count == 0


async def test_kill_switch_active_denies_with_zero_rows(pool):
    """I-02 — kill switch(항상 DENY하는 게이트) 활성 시 0행."""
    user_id = await create_test_tenant(pool)
    execution_id = await _create_running_execution(pool, user_id)
    cmd = _command(user_id, execution_id)
    entity_context = await seed_entity_context(pool, user_id)

    with pytest.raises(OrderSubmitDeniedError):
        await submit_order(
            cmd, pool=pool, profile=_profile(), registry=_registry(),
            pre_submit_gate=_kill_switch_gate, entity_context=entity_context,
            entity_repo=PostgresEntityRepository(pool),
        )

    async with pool.acquire() as conn:
        order_count = await conn.fetchval(
            "SELECT count(*) FROM orders WHERE execution_id = $1", execution_id
        )
    assert order_count == 0
