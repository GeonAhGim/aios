"""open_order_sweeper STRATEGY_DEPLOYMENT `exec:<int>` D3 보강 (DEEPEN-DEPTH R-39, task-23333).

Spec: docs/specs/L4_risk_and_safety_v1.0.md §3.8, §5(105번), §9(R-39).
`test_open_order_sweeper.py::test_strategy_deployment_exec_prefix_only_that_execution`의
공백(성능 단언 0, 게이트 적색 재현 0)을 채운다: 형식 위반 negative, 어댑터 실패주입,
종결 주문 미영향, 성능 예산과 virtual 지연 10x 적색 재현. 기존 파일은 500줄 상한
(loc_growth)이라 수정하지 않고 형제 파일로 분리했다.
"""

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

# Local measurement (2026-11-11): seed + exec-scoped sweep of one order, best-of-5 wall ~1.6ms.
# Budget 100ms = ~60x headroom (>=30x rule); wall_ms is gated (cpu_ms reads 0 on clock tick).
PERF_BUDGET_MS = 100.0


@pytest.mark.parametrize("bad_ref", ["exec:", "exec:-1", "exec:1.5", "exec: 7", "exec:1e3"])
async def test_exec_prefix_rejects_malformed_ids_without_touching_orders(pool, bad_ref):
    """negative — 빈 값/음수/소수/공백/지수 표기는 fail-closed로 거부하고 주문을 건드리지 않는다."""
    user_id = await create_test_user(pool)
    exec_id = await _seed_execution(pool, user_id)
    order_id = await _seed_order(pool, user_id, execution_id=exec_id)

    with pytest.raises(MalformedScopeRefError):
        await sweep_open_orders(
            pool, _adapters("bitget"), control_id=uuid4(),
            scope=SafetyScope.STRATEGY_DEPLOYMENT, scope_ref=bad_ref,
        )  # fmt: skip

    assert await _status_of(pool, order_id) == "SUBMITTED"


async def test_unknown_prefix_is_rejected_instead_of_matching_zero_rows(pool):
    """negative — `exec:`/`dep:` 외 접두사는 조용히 0건이 아니라 거부한다."""
    with pytest.raises(MalformedScopeRefError):
        await sweep_open_orders(
            pool, _adapters("bitget"), control_id=uuid4(),
            scope=SafetyScope.STRATEGY_DEPLOYMENT, scope_ref="execution:1",
        )  # fmt: skip


async def test_exec_prefix_unknown_execution_id_matches_nothing(pool):
    """negative — 존재하지 않는 execution id는 다른 execution의 주문을 쓸어가지 않는다."""
    user_id = await create_test_user(pool)
    exec_id = await _seed_execution(pool, user_id)
    order_id = await _seed_order(pool, user_id, execution_id=exec_id)

    report = await sweep_open_orders(
        pool, _adapters("bitget"), control_id=uuid4(),
        scope=SafetyScope.STRATEGY_DEPLOYMENT, scope_ref=f"exec:{exec_id + 10_000_000}",
    )  # fmt: skip

    assert report.cancel_requested == ()
    assert await _status_of(pool, order_id) == "SUBMITTED"


async def test_exec_prefix_terminal_order_in_target_execution_is_skipped(pool):
    """negative — 대상 execution 안의 종결 주문은 되살리지 않고 skipped로 보고한다."""
    user_id = await create_test_user(pool)
    exec_id = await _seed_execution(pool, user_id)
    filled = await _seed_order(pool, user_id, execution_id=exec_id, status="FILLED")
    open_order = await _seed_order(pool, user_id, execution_id=exec_id)

    report = await sweep_open_orders(
        pool, _adapters("bitget"), control_id=uuid4(),
        scope=SafetyScope.STRATEGY_DEPLOYMENT, scope_ref=f"exec:{exec_id}",
    )  # fmt: skip

    assert report.cancel_requested == (open_order,)
    assert filled in report.skipped
    assert await _status_of(pool, filled) == "FILLED"


async def test_exec_prefix_adapter_failure_is_reported_and_other_execution_untouched(pool):
    """failure injection — 어댑터 cancel 예외는 adapter_failed로 격리되고 타 execution은 불변."""
    user_id = await create_test_user(pool)
    target_exec = await _seed_execution(pool, user_id)
    other_exec = await _seed_execution(pool, user_id)
    target_order = await _seed_order(pool, user_id, execution_id=target_exec)
    other_order = await _seed_order(pool, user_id, execution_id=other_exec)

    report = await sweep_open_orders(
        pool, {"bitget": _RaisingCancelAdapter()}, control_id=uuid4(),
        scope=SafetyScope.STRATEGY_DEPLOYMENT, scope_ref=f"exec:{target_exec}",
    )  # fmt: skip

    assert target_order in report.adapter_failed
    assert await _status_of(pool, target_order) == "CANCEL_REQUESTED"
    assert other_order not in set(report.cancel_requested) | set(report.adapter_failed)
    assert await _status_of(pool, other_order) == "SUBMITTED"


@pytest.mark.perf
async def test_exec_prefix_sweep_perf_budget_and_injected_delay_goes_red(pool, perf_budget):
    """Perf + gate-red — exec 범위 sweep을 best_of_async로 측정해 예산 단언을 걸고,
    virtual_delayed_async 10x 지연 주입 시 같은 단언이 실제로 적색이 되는지 확인한다.
    예산 근거: 모듈 상단 PERF_BUDGET_MS 주석의 로컬 실측(~1.6ms) 대비 30x 이상 여유."""
    from tests.support.perf_inject import virtual_delayed_async

    user_id = await create_test_user(pool)
    exec_id = await _seed_execution(pool, user_id)
    adapter = _adapters("bitget")["bitget"]

    async def _sweep():
        await _seed_order(pool, user_id, execution_id=exec_id)
        return await sweep_open_orders(
            pool, {"bitget": adapter}, control_id=uuid4(),
            scope=SafetyScope.STRATEGY_DEPLOYMENT, scope_ref=f"exec:{exec_id}",
        )  # fmt: skip

    baseline = await perf_budget.best_of_async(_sweep, n=5, warmup=1)
    assert baseline.wall_ms < PERF_BUDGET_MS, perf_budget.describe(
        baseline, budget_ms=PERF_BUDGET_MS
    )

    adapter.cancel_order = virtual_delayed_async(
        adapter.cancel_order, seconds=PERF_BUDGET_MS * 10 / 1000
    )
    slow = await perf_budget.best_of_async(_sweep, n=3, warmup=0)
    assert slow.wall_ms >= PERF_BUDGET_MS * 10, "delay injection must be visible to the gate"
    assert slow.wall_ms > PERF_BUDGET_MS
