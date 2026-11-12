"""R-39 DEEPEN evidence for `test_open_order_sweeper.py::
test_strategy_deployment_exec_prefix_only_that_execution` (task-23798).

Split into a sibling file because the parent module sits at 488 lines (500-line ratchet).
Fixtures/helpers come from `tests/unit/services/conftest.py`, unchanged."""

from __future__ import annotations

from uuid import uuid4

import pytest

from src.foundation.risk_gate.domain.models import SafetyScope
from src.services.safety.legacy_execution_pauser import MalformedScopeRefError
from src.services.safety.open_order_sweeper import sweep_open_orders
from tests.integration.conftest import create_test_user
from tests.support.raw_order_isolation import isolated_raw_orders as isolated_raw_orders
from tests.unit.services.conftest import (
    _adapters,
    _RaisingCancelAdapter,
    _seed_execution,
    _seed_order,
    _status_of,
)

# Local measurement (2026-11-12): seed + sweep of one exec-scoped order, best-of-5 wall ~2ms.
# Budget 100ms = ~50x headroom (>=30x rule); same budget as the parent module (no new budget).
PERF_BUDGET_MS = 100.0


async def _sweep(pool, ref, adapters=None):
    return await sweep_open_orders(
        pool,
        _adapters("bitget") if adapters is None else adapters,
        control_id=uuid4(),
        scope=SafetyScope.STRATEGY_DEPLOYMENT,
        scope_ref=ref,
    )


@pytest.mark.parametrize(
    "evidence",
    [
        "other_exec_untouched",
        "terminal",
        "repeat",
        "malformed",
        "unknown_exec",
        "failure",
        "gate_red",
        pytest.param("perf", marks=pytest.mark.perf),
    ],
)
async def test_exec_prefix_depth_evidence(pool, perf_budget, evidence):
    user_id = await create_test_user(pool)
    target_exec = await _seed_execution(pool, user_id)
    other_exec = await _seed_execution(pool, user_id)
    target = await _seed_order(pool, user_id, execution_id=target_exec)
    other = await _seed_order(pool, user_id, execution_id=other_exec)
    ref = f"exec:{target_exec}"

    if evidence == "other_exec_untouched":
        assert (await _sweep(pool, ref)).cancel_requested == (target,)
        assert await _status_of(pool, other) == "SUBMITTED"
    elif evidence == "terminal":
        done = await _seed_order(pool, user_id, execution_id=target_exec, status="FILLED")
        result = await _sweep(pool, ref)
        assert done not in result.cancel_requested
        assert await _status_of(pool, done) == "FILLED"
    elif evidence == "repeat":
        await _sweep(pool, ref)
        assert (await _sweep(pool, ref)).cancel_requested == ()
        assert await _status_of(pool, target) == "CANCEL_REQUESTED"
    elif evidence == "malformed":
        with pytest.raises(MalformedScopeRefError):
            await _sweep(pool, "exec:not-an-int")
        assert await _status_of(pool, target) == "SUBMITTED"
    elif evidence == "unknown_exec":
        result = await _sweep(pool, f"exec:{other_exec + 10_000_000}")
        assert result.cancel_requested == ()
        assert await _status_of(pool, target) == "SUBMITTED"
    elif evidence == "failure":
        adapter = _RaisingCancelAdapter(exchange_name="bitget")
        result = await _sweep(pool, ref, {"bitget": adapter})
        assert result.adapter_failed == (target,)
        assert adapter.cancel_call_count == 1
        assert await _status_of(pool, other) == "SUBMITTED"
    elif evidence == "gate_red":
        # Feeding the other execution must flip the isolation assertions red.
        report = await _sweep(pool, f"exec:{other_exec}")
        with pytest.raises(AssertionError):
            assert report.cancel_requested == (target,)
        with pytest.raises(AssertionError):
            assert await _status_of(pool, other) == "SUBMITTED"
    else:
        from tests.support.perf_inject import virtual_delayed_async

        adapter = _adapters("bitget")["bitget"]

        async def measured_sweep():
            pending = await _seed_order(pool, user_id, execution_id=target_exec)
            result = await _sweep(pool, ref, {"bitget": adapter})
            assert pending in result.cancel_requested
            assert other not in result.cancel_requested
            return result

        def assert_budget(sample):
            assert sample.wall_ms < PERF_BUDGET_MS, perf_budget.describe(
                sample, budget_ms=PERF_BUDGET_MS
            )

        assert_budget(await perf_budget.best_of_async(measured_sweep, n=5, warmup=1))
        adapter.cancel_order = virtual_delayed_async(
            adapter.cancel_order, seconds=PERF_BUDGET_MS * 10 / 1000
        )
        slow = await perf_budget.best_of_async(measured_sweep, n=3, warmup=0)
        assert slow.wall_ms >= PERF_BUDGET_MS * 10
        with pytest.raises(AssertionError, match="budget<"):
            assert_budget(slow)
        assert await _status_of(pool, other) == "SUBMITTED"
