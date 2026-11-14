"""L24 depth supplement (D2): negative, failure injection, gate-red and perf for the tick.

Target: `src/services/execution_loop/tick.py`. Complements `test_tick_v2.py` (not modified).

Perf budget (new, this file only): no-signal tick measured ~1.3 ms wall (p95 1.5 ms) locally
(best-of-5, batch=4, 2026-11-14). `_NO_SIGNAL_BUDGET_MS` = 100 ms (~77x headroom, >=30x rule);
the gate-red test injects a virtual 10x-of-budget delay via `tests.support.perf_inject` and
proves the same assertion goes red.
"""

from __future__ import annotations

import statistics
from decimal import Decimal

import asyncpg
import pytest

from src.data.models.trading import AccountBalance, OrderStatus
from src.services.execution_loop.tick import run_execution_tick
from tests.integration._execution_tick_helpers import _create_execution, _engines, _make_pool
from tests.integration.conftest import create_test_tenant
from tests.integration.fake_exchange_adapter import FakeExchangeAdapter
from tests.performance.pre_trade_latency_support import percentile
from tests.support.perf_inject import virtual_delayed_async
from tests.support.raw_order_isolation import isolated_raw_orders as isolated_raw_orders


@pytest.fixture
async def pool(isolated_raw_orders):
    p = await _make_pool()
    yield p
    await p.close()


def _filled_adapter(close: Decimal = Decimal("50")) -> FakeExchangeAdapter:
    return FakeExchangeAdapter(
        closes=[close] * 65,
        place_order_result_status=OrderStatus.FILLED,
        usdt_balance=AccountBalance(
            exchange="bitget", asset="USDT", total=Decimal("10000"), available=Decimal("10000")
        ),
    )


async def _order_count(pool: asyncpg.Pool, execution_id: int) -> int:
    async with pool.acquire() as conn:
        return int(
            await conn.fetchval("SELECT COUNT(*) FROM orders WHERE execution_id = $1", execution_id)
        )


async def _fsm_state(pool: asyncpg.Pool, execution_id: int) -> str:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT fsm_state FROM strategy_executions WHERE id = $1", execution_id
        )


# --- negative (4) -------------------------------------------------------------


async def test_negative_unknown_execution_id_raises_before_exchange_call(pool):
    adapter = FakeExchangeAdapter(closes=[Decimal("50")] * 65)
    with pytest.raises(ValueError, match="존재하지 않는 실행입니다"):
        await run_execution_tick(pool, adapter, 987654321, **_engines())
    assert adapter.place_order_call_count == 0


async def test_negative_paused_execution_creates_no_order_and_keeps_idle(pool):
    user_id = await create_test_tenant(pool)
    execution_id = await _create_execution(pool, user_id, entry_threshold=100.0)
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE strategy_executions SET paused_by = 'USER' WHERE id = $1", execution_id
        )
    adapter = _filled_adapter()

    await run_execution_tick(pool, adapter, execution_id, **_engines())

    assert adapter.place_order_call_count == 0
    assert await _order_count(pool, execution_id) == 0
    assert await _fsm_state(pool, execution_id) == "IDLE"


async def test_negative_no_signal_tick_writes_no_orders(pool):
    user_id = await create_test_tenant(pool)
    execution_id = await _create_execution(pool, user_id, entry_threshold=-1.0)
    adapter = _filled_adapter()

    await run_execution_tick(pool, adapter, execution_id, **_engines())

    assert adapter.place_order_call_count == 0
    assert await _order_count(pool, execution_id) == 0
    assert await _fsm_state(pool, execution_id) == "IDLE"


async def test_negative_restricted_circuit_breaker_blocks_entry(pool):
    user_id = await create_test_tenant(pool)
    execution_id = await _create_execution(pool, user_id, entry_threshold=100.0)
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE system_safety_state SET circuit_breaker_level = 'restricted' WHERE id = 1"
        )
    adapter = _filled_adapter()

    await run_execution_tick(pool, adapter, execution_id, **_engines())

    assert adapter.place_order_call_count == 0
    assert await _order_count(pool, execution_id) == 0


# --- failure injection (1) ----------------------------------------------------


async def test_failure_injection_ohlcv_fetch_error_propagates_without_side_effects(
    pool, monkeypatch
):
    user_id = await create_test_tenant(pool)
    execution_id = await _create_execution(pool, user_id, entry_threshold=100.0)
    adapter = _filled_adapter()

    async def _boom(*args: object, **kwargs: object) -> object:
        raise ConnectionError("injected ohlcv outage")

    monkeypatch.setattr(adapter, "get_ohlcv", _boom)

    with pytest.raises(ConnectionError, match="injected ohlcv outage"):
        await run_execution_tick(pool, adapter, execution_id, **_engines())

    assert adapter.place_order_call_count == 0
    assert await _order_count(pool, execution_id) == 0
    assert await _fsm_state(pool, execution_id) == "IDLE"


# --- gate red reproduction (1) ------------------------------------------------


async def test_gate_red_no_order_assertion_catches_a_signalling_tick(pool):
    """The "no order written" assertion used by the negative tests must go red when the input is
    broken (entry condition always true) — otherwise those tests could never fail."""
    user_id = await create_test_tenant(pool)
    execution_id = await _create_execution(pool, user_id, entry_threshold=100.0)
    adapter = _filled_adapter()

    await run_execution_tick(pool, adapter, execution_id, **_engines())

    with pytest.raises(AssertionError):
        assert adapter.place_order_call_count == 0
    with pytest.raises(AssertionError):
        assert await _order_count(pool, execution_id) == 0


# --- performance (perf marker, best-of + virtual 10x injection) ---------------

_NO_SIGNAL_BUDGET_MS = 100.0


async def _best_no_signal_ms(
    pool, perf_budget, *, inject_ms: float = 0.0
) -> tuple[float, list[float]]:
    user_id = await create_test_tenant(pool)
    execution_id = await _create_execution(pool, user_id, entry_threshold=-1.0)
    adapter = FakeExchangeAdapter(closes=[Decimal("50")] * 65)
    if inject_ms:
        adapter.get_ohlcv = virtual_delayed_async(adapter.get_ohlcv, inject_ms / 1000)  # type: ignore[method-assign]
    engines = _engines()

    async def _tick() -> None:
        await run_execution_tick(pool, adapter, execution_id, **engines)

    await _tick()  # warm-up
    samples = await perf_budget.samples_async(_tick, n=5, batch=4)
    walls = [s.wall_ms for s in samples]
    return min(walls), walls


@pytest.mark.perf
async def test_performance_no_signal_tick_best_of_budget(pool, perf_budget):
    best, walls = await _best_no_signal_ms(pool, perf_budget)
    print(
        f"\nno-signal tick wall best={best:.2f}ms p50={statistics.median(walls):.2f}ms "
        f"p95={percentile(walls, 95):.2f}ms budget<{_NO_SIGNAL_BUDGET_MS}ms"
    )
    assert best < _NO_SIGNAL_BUDGET_MS


@pytest.mark.perf
async def test_performance_gate_red_virtual_10x_delay_breaches_budget(pool, perf_budget):
    """Gate-red: a virtual delay of 10x the budget on get_ohlcv must break the same assertion."""
    best, _ = await _best_no_signal_ms(pool, perf_budget, inject_ms=_NO_SIGNAL_BUDGET_MS * 10)
    assert best >= _NO_SIGNAL_BUDGET_MS, "perf assertion failed to catch a 10x injected delay"
