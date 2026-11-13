"""EM-16 depth floor (D1 -> D2) -- supplementary adversarial tests for the
`AlgoRunPlan` reservation path in `application/tick_algo.py`.

Sibling of `test_overcommit_and_terminal_races.py` (unchanged): same fixtures
and helpers, imported rather than copied. Each test attacks an angle the
original file does not cover. Contents: negative x4, failure injection x1,
gate-red reproduction x1 (10x virtual delay), performance assertion x1.

Performance budget (new): one tick against a fresh parent, best-of-5 wall_ms,
local Postgres measurement ~1.9 ms -> budget 500 ms (>30x headroom,
tolerates CI contention while still catching an O(n^2) or lock-leak regression).
"""

# ruff: noqa: F811
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from src.foundation.ems.application.start_algo import AlgoRunPlan
from src.foundation.ems.application.tick_algo import tick_algo
from src.services.oms.adapters.order_repository import PostgresOrderRepository
from tests.adversarial.ems.test_overcommit_and_terminal_races import (
    _CONCURRENT_RUNS,
    _child,
    _committed_qty,
    _insert_order,
    _parent_order,
    _plan_with_one_slice,
    _status,
    _Submitter,
    _tick,
    pool,  # noqa: F401 -- fixture re-exported
)
from tests.integration.oms.conftest import create_test_user
from tests.support.perf_inject import reset_virtual_totals, virtual_delayed_async
from tests.support.raw_order_isolation import isolated_raw_orders as isolated_raw_orders

_BUDGET_MS = 500.0


# -- negative ------------------------------------------------------------------


async def test_partial_fill_does_not_shrink_reservation_capacity(pool):
    """Negative 1 -- capacity is `parent qty - committed_child_qty`, not
    `qty - filled`: with filled=3, two concurrent 5-slices both fit (10)."""
    user_id = await create_test_user(pool)
    parent_id = await _insert_order(pool, user_id, quantity=Decimal("10"))
    await pool.execute("UPDATE orders SET filled_quantity = 3 WHERE order_id = $1", parent_id)
    repo, now = PostgresOrderRepository(), datetime.now(UTC)

    results = await asyncio.gather(
        _tick(_plan_with_one_slice(parent_id, Decimal("5")), pool, repo, _Submitter(), now),
        _tick(_plan_with_one_slice(parent_id, Decimal("5")), pool, repo, _Submitter(), now),
    )

    assert all(r.outcomes[0].order is not None for r in results)
    assert await _committed_qty(pool, parent_id) == Decimal("10")


async def test_fully_committed_parent_denies_before_submit(pool):
    """Negative 2 -- committed_child_qty already == qty: any further slice is
    denied and `submit_child` is never reached."""
    user_id = await create_test_user(pool)
    parent_id = await _insert_order(
        pool, user_id, quantity=Decimal("10"), committed_child_qty=Decimal("10")
    )
    submitter = _Submitter()

    result = await _tick(
        _plan_with_one_slice(parent_id, Decimal("1")),
        pool,
        PostgresOrderRepository(),
        submitter,
        datetime.now(UTC),
    )

    assert result.outcomes[0].order is None
    assert result.outcomes[0].denied_reason is not None
    assert submitter.calls == []
    assert await _committed_qty(pool, parent_id) == Decimal("10")


async def test_single_oversized_slice_denied_without_reservation(pool):
    """Negative 3 -- one slice larger than the whole parent (12 > 10) is
    denied outright and leaves committed_child_qty at 0."""
    user_id = await create_test_user(pool)
    parent_id = await _insert_order(pool, user_id, quantity=Decimal("10"))
    submitter = _Submitter()

    result = await _tick(
        _plan_with_one_slice(parent_id, Decimal("12")),
        pool,
        PostgresOrderRepository(),
        submitter,
        datetime.now(UTC),
    )

    assert result.outcomes[0].order is None
    assert "exceeding parent qty" in result.outcomes[0].denied_reason
    assert submitter.calls == []
    assert await _committed_qty(pool, parent_id) == Decimal("0")


async def test_cancelled_plan_tick_is_a_noop(pool):
    """Negative 4 -- `plan.cancelled` (EM-A4) short-circuits the tick: no
    outcomes, no submit, no reservation, even for a due slice."""
    user_id = await create_test_user(pool)
    parent_id = await _insert_order(pool, user_id, quantity=Decimal("10"))
    now = datetime.now(UTC)
    plan = AlgoRunPlan(
        parent=_parent_order(parent_id, qty=Decimal("10")),
        children=[_child(parent_id, 0, Decimal("1"), scheduled_at=now - timedelta(seconds=1))],
    )
    plan.cancelled = True
    submitter = _Submitter()

    result = await _tick(plan, pool, PostgresOrderRepository(), submitter, now)

    assert result.outcomes == []
    assert submitter.calls == []
    assert await _committed_qty(pool, parent_id) == Decimal("0")
    assert await _status(pool, parent_id) == "ACKNOWLEDGED"


# -- failure injection ---------------------------------------------------------


async def test_both_concurrent_submits_fail_releases_every_reservation(pool):
    """Failure injection -- two concurrent boundary reservations (5+5) both
    win, then both `submit_child` calls raise: every reservation is released."""
    user_id = await create_test_user(pool)
    parent_id = await _insert_order(pool, user_id, quantity=Decimal("10"))
    repo, now = PostgresOrderRepository(), datetime.now(UTC)
    failing = _Submitter(fail_with={0: ConnectionError("simulated venue outage")})

    results = await asyncio.gather(
        _tick(_plan_with_one_slice(parent_id, Decimal("5")), pool, repo, failing, now),
        _tick(_plan_with_one_slice(parent_id, Decimal("5")), pool, repo, failing, now),
    )

    for r in results:
        assert r.outcomes[0].order is None
        assert r.outcomes[0].denied_reason is not None
    assert await _committed_qty(pool, parent_id) == Decimal("0")


# -- performance + gate-red ----------------------------------------------------


async def _fresh_tick_runner(pool, runs: int, delay_s: float = 0.0):
    """Return an async callable that ticks one fresh parent per invocation."""
    user_id = await create_test_user(pool)
    repo, now = PostgresOrderRepository(), datetime.now(UTC)
    parents = [await _insert_order(pool, user_id, quantity=Decimal("10")) for _ in range(runs)]
    submitter = _Submitter()
    submit = virtual_delayed_async(submitter, delay_s) if delay_s else submitter

    async def _run():
        plan = _plan_with_one_slice(parents.pop(), Decimal("1"))
        return await tick_algo(plan, pool=pool, orders_repo=repo, submit_child=submit, now=now)

    return _run


@pytest.mark.perf
async def test_single_tick_best_of_wall_within_budget(pool, perf_budget):
    """Performance -- best-of-5 wall time of one tick stays under budget."""
    reset_virtual_totals()
    run = await _fresh_tick_runner(pool, runs=_CONCURRENT_RUNS)

    sample = await perf_budget.best_of_async(run, n=5, warmup=1)

    assert sample.wall_ms < _BUDGET_MS, perf_budget.describe(sample, budget_ms=_BUDGET_MS)


@pytest.mark.perf
async def test_gate_red_virtual_delay_10x_breaches_budget(pool, perf_budget):
    """Gate-red reproduction -- a virtual delay of 10x the budget injected into
    `submit_child` must push the same measurement over `_BUDGET_MS`; proves the
    performance assertion above actually catches a regression."""
    reset_virtual_totals()
    run = await _fresh_tick_runner(pool, runs=_CONCURRENT_RUNS, delay_s=_BUDGET_MS * 10 / 1000)

    sample = await perf_budget.best_of_async(run, n=5, warmup=1)

    with pytest.raises(AssertionError):
        assert sample.wall_ms < _BUDGET_MS, perf_budget.describe(sample, budget_ms=_BUDGET_MS)
    reset_virtual_totals()
