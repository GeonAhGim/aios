"""L24 depth floor D1->D2 -- extra evidence for `src/services/execution_loop/tick.py`.

Sibling of `test_tick_v2.py` (unchanged); reuses its fixtures and helpers. Adds negative
cases for corrupt or dangling strategy context, failure injection on the exchange adapter
and a best-of-N perf budget with a 10x virtual-delay gate-red reproduction.

Perf budget (new): `_NO_SIGNAL_BUDGET_MS`, locally measured 1.5ms (best of 5,
no-signal tick, wall); the budget keeps a >100x margin (>=30x required).
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from src.services.execution_loop.tick import run_execution_tick
from tests.integration.conftest import create_test_tenant
from tests.integration.fake_exchange_adapter import FakeExchangeAdapter
from tests.integration.test_execution_tick import _create_execution, _engines
from tests.integration.test_tick_v2 import (
    _create_crossover_execution,
    _filled_adapter,
)
from tests.integration.test_tick_v2 import (
    isolated_raw_orders as isolated_raw_orders,
)
from tests.integration.test_tick_v2 import (
    pool as pool,
)
from tests.support.perf_budget import PerfBudget
from tests.support.perf_inject import reset_virtual_totals, virtual_delayed_async

# Measured 1.5ms (best of 5, wall, local, 2026-11-14); 200ms is >100x headroom.
_NO_SIGNAL_BUDGET_MS = 200.0


async def _fsm_state(pool, execution_id: int) -> str:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT fsm_state FROM strategy_executions WHERE id = $1", execution_id
        )


async def test_negative_corrupt_fsm_definition_rejected_without_exchange_call(pool):
    """negative-1: an unparsable fsm_definition fails closed before any adapter call."""
    user_id = await create_test_tenant(pool)
    execution_id = await _create_crossover_execution(pool, user_id)
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE strategies SET fsm_definition = $1::jsonb "
            "WHERE strategy_id = (SELECT strategy_id FROM strategy_executions WHERE id = $2)",
            json.dumps({"bogus": True}),
            execution_id,
        )
    adapter = _filled_adapter(Decimal("40"))

    with pytest.raises(Exception) as excinfo:
        await run_execution_tick(pool, adapter, execution_id, **_engines())

    assert not isinstance(excinfo.value, AssertionError)
    assert adapter.place_order_call_count == 0
    assert await _fsm_state(pool, execution_id) == "IDLE"


async def test_negative_paused_crossover_never_submits_or_transitions(pool):
    """negative-2: a paused execution ignores a live crossover, with fsm_state untouched."""
    user_id = await create_test_tenant(pool)
    execution_id = await _create_crossover_execution(pool, user_id)
    await run_execution_tick(
        pool,
        FakeExchangeAdapter(closes=[Decimal("60")] * 65),
        execution_id,
        **(engines := _engines()),
    )
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE strategy_executions SET paused_by = 'SAFETY_LAYER' WHERE id = $1", execution_id
        )
    adapter = _filled_adapter(Decimal("40"))

    assert await run_execution_tick(pool, adapter, execution_id, **engines) is None

    assert adapter.place_order_call_count == 0
    assert await _fsm_state(pool, execution_id) == "IDLE"


async def test_negative_unknown_negative_execution_ids_never_submit(pool):
    """negative-3: sentinel/unknown ids (0, -1, huge) all raise and never reach the exchange."""
    for bad_id in (0, -1, 2**62):
        adapter = _filled_adapter(Decimal("40"))
        with pytest.raises(ValueError, match="존재하지 않는 실행입니다"):
            await run_execution_tick(pool, adapter, bad_id, **_engines())
        assert adapter.place_order_call_count == 0


async def test_failure_injection_ohlcv_outage_leaves_fsm_idle_and_submits_nothing(
    pool, monkeypatch
):
    """failure injection: a market-data outage mid-tick propagates and changes no state."""
    user_id = await create_test_tenant(pool)
    execution_id = await _create_crossover_execution(pool, user_id)
    adapter = _filled_adapter(Decimal("40"))

    async def _boom(*args: object, **kwargs: object) -> object:
        raise ConnectionError("injected ohlcv outage")

    monkeypatch.setattr(adapter, "get_ohlcv", _boom)

    with pytest.raises(ConnectionError, match="injected ohlcv outage"):
        await run_execution_tick(pool, adapter, execution_id, **_engines())

    assert adapter.place_order_call_count == 0
    assert await _fsm_state(pool, execution_id) == "IDLE"


async def _measure_no_signal(perf_budget: PerfBudget, pool, *, delay_s: float = 0.0):
    user_id = await create_test_tenant(pool)
    execution_id = await _create_execution(pool, user_id, entry_threshold=-1.0)
    adapter = FakeExchangeAdapter(closes=[Decimal("50")] * 65)
    engines = _engines()

    async def tick() -> None:
        await run_execution_tick(pool, adapter, execution_id, **engines)

    call = virtual_delayed_async(tick, delay_s) if delay_s else tick
    return await perf_budget.best_of_async(call, n=5, warmup=1)


@pytest.mark.perf
async def test_perf_no_signal_tick_best_of_budget(perf_budget: PerfBudget, pool):
    """perf: best-of-5 no-signal tick wall latency stays under the budget."""
    reset_virtual_totals()
    sample = await _measure_no_signal(perf_budget, pool)
    print(
        f"\nno-signal tick best-of-5 {perf_budget.describe(sample, budget_ms=_NO_SIGNAL_BUDGET_MS)}"
    )
    assert sample.wall_ms < _NO_SIGNAL_BUDGET_MS, perf_budget.describe(
        sample, budget_ms=_NO_SIGNAL_BUDGET_MS
    )


@pytest.mark.perf
async def test_perf_no_signal_tick_gate_red_with_10x_injection(perf_budget: PerfBudget, pool):
    """gate-red: a virtual 10x-budget delay makes the same assertion fail."""
    reset_virtual_totals()
    sample = await _measure_no_signal(perf_budget, pool, delay_s=_NO_SIGNAL_BUDGET_MS * 10 / 1000)
    assert not (sample.wall_ms < _NO_SIGNAL_BUDGET_MS)
    reset_virtual_totals()
