"""L4-10 (task-2351) -- Executor no longer self-generates client_order_id.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md#§9 L4-10 [FROZEN_PAPER_ONLY]

DoD (b): retrying the same intent (same execution_id + unchanged
`strategy_executions.intent_counter`) calls the exchange adapter exactly
once and reuses the same `client_order_id` for the second attempt -- the
deterministic id from `services/oms/domain/idempotency.py` (L4-03) plus the
existing `orders.client_order_id` UNIQUE claim in `order_service.submit`
absorb the retry before it ever reaches the adapter.
"""
from __future__ import annotations

import json
import uuid
from decimal import Decimal
from pathlib import Path

import asyncpg
import pytest
from dotenv import dotenv_values

from src.core.executor.executor import _CLIENT_ORDER_ID_CHARSET, Executor
from src.core.portfolio.models import AllocationDecision
from src.core.risk.models import RiskCheckResult
from src.data.models.strategy_fsm import FSMState
from src.data.models.trading import OrderSide
from src.services.condition_compiler import ConditionCompiler
from src.services.order_service.gate import GateDecision, GateOutcome
from src.services.preview_service import PreviewCondition
from tests.integration.conftest import create_test_tenant
from tests.integration.fake_exchange_adapter import FakeExchangeAdapter


async def _allow_gate(context: object) -> GateDecision:
    return GateDecision(
        outcome=GateOutcome.ALLOW, decision_id=uuid.uuid4(), compliance_decision_id=uuid.uuid4()
    )


def _asyncpg_dsn() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[3] / ".env")
    url = env.get("DATABASE_URL")
    assert url
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=2)
    yield p
    await p.close()


def _fsm_config():
    return ConditionCompiler().compile(
        strategy_id="strat-cid-test",
        version="1.0.0",
        target_asset="BTC/USDT",
        market="crypto",
        exchange="bitget",
        author_agent="test",
        entry_conditions=[PreviewCondition(indicator="RSI", operator="<", threshold=30.0)],
        exit_conditions=[PreviewCondition(indicator="RSI", operator=">", threshold=70.0)],
        stop_loss_conditions=[PreviewCondition(indicator="RSI", operator="<", threshold=10.0)],
    )


def _allocation() -> AllocationDecision:
    return AllocationDecision(
        symbol="BTC/USDT",
        strategy_id="strat-cid-test",
        approved_quantity=Decimal("0.01"),
        capital_pct=Decimal("10"),
    )


def _approved_risk_result() -> RiskCheckResult:
    return RiskCheckResult(approved=True, rejection_reason=None, checked_rules=["daily_loss"])


async def _create_execution(pool: asyncpg.Pool, user_id: uuid.UUID) -> int:
    strategy_id = f"executor-cid-test-{uuid.uuid4().hex[:8]}"
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
                 allocated_capital, currency, status, fsm_state)
            VALUES ($1, '1.0.0', $2, 'bitget', 'PAPER', 100, 'USDT', 'RUNNING',
                    'BUY_ORDER_PENDING')
            RETURNING id
            """,
            strategy_id,
            user_id,
        )
    return row["id"]


async def _noop_fsm_state_writer(execution_id: int, expected: FSMState, new: FSMState) -> None:
    raise AssertionError("adapter never returns FILLED in this test -- writer must not be called")


async def _execute_once(
    pool: asyncpg.Pool, adapter: FakeExchangeAdapter, execution_id: int, user_id: uuid.UUID
):
    return await Executor().execute(
        _allocation(),
        _approved_risk_result(),
        adapter,
        execution_id=execution_id,
        user_id=user_id,
        strategy_version="1.0.0",
        mode="PAPER",
        side=OrderSide.BUY,
        pending_fsm_state=FSMState.BUY_ORDER_PENDING,
        fsm_config=_fsm_config(),
        fsm_state_writer=_noop_fsm_state_writer,
        pool=pool,
        pre_submit_gate=_allow_gate,
    )


async def test_retrying_the_same_intent_calls_adapter_once_and_reuses_client_order_id(pool):
    user_id = await create_test_tenant(pool)
    execution_id = await _create_execution(pool, user_id)
    adapter = FakeExchangeAdapter()

    first = await _execute_once(pool, adapter, execution_id, user_id)
    second = await _execute_once(pool, adapter, execution_id, user_id)

    assert adapter.place_order_call_count == 1
    assert second.client_order_id == first.client_order_id
    assert second.order_id == first.order_id


async def test_a_new_intent_counter_derives_a_different_client_order_id(pool):
    user_id = await create_test_tenant(pool)
    execution_id = await _create_execution(pool, user_id)
    adapter = FakeExchangeAdapter()

    first = await _execute_once(pool, adapter, execution_id, user_id)

    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE strategy_executions SET intent_counter = intent_counter + 1 WHERE id = $1",
            execution_id,
        )

    second = await _execute_once(pool, adapter, execution_id, user_id)

    assert adapter.place_order_call_count == 2
    assert second.client_order_id != first.client_order_id
    assert second.order_id != first.order_id


async def test_client_order_id_respects_the_configured_charset_and_max_len(pool):
    user_id = await create_test_tenant(pool)
    execution_id = await _create_execution(pool, user_id)
    adapter = FakeExchangeAdapter()

    submitted = await _execute_once(pool, adapter, execution_id, user_id)

    assert len(submitted.client_order_id) <= 40
    assert all(c in _CLIENT_ORDER_ID_CHARSET for c in submitted.client_order_id)
