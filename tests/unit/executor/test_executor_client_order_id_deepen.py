"""DEEPEN task-2806 -- task-2351 (L4-10) graded D1 against its D3 axis floor
(docs/audit/DEPTH_L4_BR.md#2351): `test_executor_client_order_id.py` had only
3 happy-path/idempotency tests. Adds the missing D2/D3 evidence -- negative
tests, a failure-injection test, a numeric performance assertion, a gate/CI
red-line regression test and a multi-instance/concurrent-retry adversarial
proof -- without touching `executor.py`/`idempotency.py`, mirroring the
task-2765/task-2769/task-2805 DEEPEN convention.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md#§9 L4-10 [FROZEN_PAPER_ONLY]
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import asyncpg
import pytest
from dotenv import dotenv_values

from src.core.executor.executor import (
    _CLIENT_ORDER_ID_CHARSET,
    _CLIENT_ORDER_ID_MAX_LEN,
    _IDEMPOTENCY_WINDOW_SECONDS,
    Executor,
)
from src.core.portfolio.models import AllocationDecision
from src.core.risk.models import RiskCheckResult
from src.data.models.strategy_fsm import FSMState
from src.data.models.trading import OrderSide, OrderStatus
from src.exchanges.common.error_taxonomy import ExchangeError, ExchangeErrorKind
from src.services.condition_compiler import ConditionCompiler
from src.services.oms.domain.idempotency import build_scope
from src.services.oms.domain.idempotency import client_order_id as derive_client_order_id
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
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=10)
    yield p
    await p.close()


def _fsm_config():
    return ConditionCompiler().compile(
        strategy_id="strat-cid-deepen-test",
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
        strategy_id="strat-cid-deepen-test",
        approved_quantity=Decimal("0.01"),
        capital_pct=Decimal("10"),
    )


def _approved_risk_result() -> RiskCheckResult:
    return RiskCheckResult(approved=True, rejection_reason=None, checked_rules=["daily_loss"])


async def _create_execution(pool: asyncpg.Pool, user_id: uuid.UUID) -> int:
    strategy_id = f"executor-cid-deepen-{uuid.uuid4().hex[:8]}"
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


# ---------------------------------------------------------------------------
# Negative tests (>=3)
# ---------------------------------------------------------------------------


async def test_nonexistent_execution_id_raises_value_error_before_any_order_is_placed(pool):
    adapter = FakeExchangeAdapter()

    with pytest.raises(ValueError, match="존재하지 않는 실행"):
        await _execute_once(pool, adapter, execution_id=-1, user_id=uuid.uuid4())

    assert adapter.place_order_call_count == 0


def test_derive_client_order_id_rejects_non_positive_max_len():
    scope = build_scope(
        tenant_id=uuid.uuid4(),
        account_ref="",
        provider="bitget",
        strategy_id="neg-test",
        strategy_version="1.0.0",
        execution_id=1,
        intent_seq=0,
        window_start=datetime.now(timezone.utc),
    )
    with pytest.raises(ValueError, match="max_len"):
        derive_client_order_id(scope, max_len=0, charset=_CLIENT_ORDER_ID_CHARSET)


def test_derive_client_order_id_rejects_empty_charset():
    scope = build_scope(
        tenant_id=uuid.uuid4(),
        account_ref="",
        provider="bitget",
        strategy_id="neg-test",
        strategy_version="1.0.0",
        execution_id=1,
        intent_seq=0,
        window_start=datetime.now(timezone.utc),
    )
    with pytest.raises(ValueError, match="charset"):
        derive_client_order_id(scope, max_len=_CLIENT_ORDER_ID_MAX_LEN, charset="")


# ---------------------------------------------------------------------------
# Failure-injection test
# ---------------------------------------------------------------------------


async def test_adapter_exception_leaves_terminal_failed_order_and_retry_does_not_resend(pool):
    """R1 (§1) -- a not-sent exchange failure (circuit OPEN) must not be
    silently retried as a fresh send: the claimed `client_order_id` is
    already permanently taken, so a same-intent retry after the failure
    resolves to the existing terminal FAILED row instead of ever calling
    `adapter.place_order` a second time."""
    user_id = await create_test_tenant(pool)
    execution_id = await _create_execution(pool, user_id)

    async def _circuit_open(order: object) -> object:
        raise ExchangeError(ExchangeErrorKind.SERVER_ERROR, circuit_open=True, venue="bitget")

    adapter = FakeExchangeAdapter(on_place_order=_circuit_open)

    with pytest.raises(ExchangeError):
        await _execute_once(pool, adapter, execution_id, user_id)

    assert adapter.place_order_call_count == 1

    retried = await _execute_once(pool, adapter, execution_id, user_id)

    assert adapter.place_order_call_count == 1  # never called again
    assert retried.status == OrderStatus.FAILED


# ---------------------------------------------------------------------------
# Numeric performance assertion
# ---------------------------------------------------------------------------


def test_deriving_5000_client_order_ids_stays_under_latency_budget_and_never_collides():
    tenant_id = uuid.uuid4()
    window_start = datetime.now(timezone.utc)
    latencies: list[float] = []
    ids: set[str] = set()

    for intent_seq in range(5000):
        scope = build_scope(
            tenant_id=tenant_id,
            account_ref="",
            provider="bitget",
            strategy_id="perf-test",
            strategy_version="1.0.0",
            execution_id=1,
            intent_seq=intent_seq,
            window_start=window_start,
        )
        started = time.perf_counter()
        cid = derive_client_order_id(
            scope, max_len=_CLIENT_ORDER_ID_MAX_LEN, charset=_CLIENT_ORDER_ID_CHARSET
        )
        latencies.append(time.perf_counter() - started)
        ids.add(cid)

    assert len(ids) == 5000  # 5,000 distinct intents -> 5,000 distinct ids, no collision

    latencies.sort()
    total = sum(latencies)
    p95 = latencies[int(len(latencies) * 0.95)]
    assert total < 1.0  # 5,000 pure sha256-based derivations complete well under 1s total
    assert p95 < 0.001  # p95 per-derivation latency under 1ms (no I/O in this path)


# ---------------------------------------------------------------------------
# Gate/CI red-line regression test
# ---------------------------------------------------------------------------


class _ScriptedClock:
    """Replays a fixed sequence of `datetime.now()` results so a test can
    drive `Executor.execute()` through real wall-clock drift deterministically.
    `fromtimestamp` delegates to the real `datetime` class (only `.now()` is
    scripted) since `_floor_to_window` needs it too."""

    def __init__(self, instants: list[datetime]) -> None:
        self._instants = list(instants)

    def now(self, tz: object = None) -> datetime:
        return self._instants.pop(0)

    def fromtimestamp(self, *args: object, **kwargs: object) -> datetime:
        return datetime.fromtimestamp(*args, **kwargs)  # type: ignore[arg-type]


async def test_wall_clock_drift_inside_one_window_does_not_break_idempotency_but_crossing_it_does(
    pool, monkeypatch
):
    """Regression proof for the exact defect this leaf (task-2351) fixed:
    the old `client_order_id` was derived from `execution_id:state:now()`,
    so *any* wall-clock drift between a retry and its original attempt --
    even a single microsecond -- minted a brand-new id. Here two calls are
    54 seconds apart (same 60s window) and must still collapse to one
    adapter call; a third call one window later must genuinely be treated
    as a new intent."""
    user_id = await create_test_tenant(pool)
    execution_id = await _create_execution(pool, user_id)
    adapter = FakeExchangeAdapter()

    now_epoch = int(time.time())
    window_start_epoch = now_epoch - (now_epoch % _IDEMPOTENCY_WINDOW_SECONDS)
    t_a = datetime.fromtimestamp(window_start_epoch + 1, tz=timezone.utc)
    t_b = datetime.fromtimestamp(window_start_epoch + 55, tz=timezone.utc)
    t_c = datetime.fromtimestamp(
        window_start_epoch + _IDEMPOTENCY_WINDOW_SECONDS + 5, tz=timezone.utc
    )

    monkeypatch.setattr(
        "src.core.executor.executor.datetime", _ScriptedClock([t_a, t_b, t_c])
    )

    first = await _execute_once(pool, adapter, execution_id, user_id)
    second = await _execute_once(pool, adapter, execution_id, user_id)
    third = await _execute_once(pool, adapter, execution_id, user_id)

    assert second.client_order_id == first.client_order_id
    assert third.client_order_id != first.client_order_id
    assert adapter.place_order_call_count == 2


# ---------------------------------------------------------------------------
# Multi-instance / concurrent-retry adversarial proof (D3)
# ---------------------------------------------------------------------------


async def test_50_concurrent_retries_of_the_same_intent_call_the_adapter_exactly_once(pool):
    user_id = await create_test_tenant(pool)
    execution_id = await _create_execution(pool, user_id)
    adapter = FakeExchangeAdapter()

    results = await asyncio.gather(
        *[_execute_once(pool, adapter, execution_id, user_id) for _ in range(50)]
    )

    assert adapter.place_order_call_count == 1
    assert len({r.order_id for r in results}) == 1
    assert len({r.client_order_id for r in results}) == 1
