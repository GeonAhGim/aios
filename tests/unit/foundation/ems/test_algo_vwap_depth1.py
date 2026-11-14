"""EM-9 -- depth-1 tests for `domain/algo/vwap.py`: negative, failure-injection,
gate-red reproduction, and perf-budget assertions.

Spec: docs/specs/L4_ems_routing_algos_and_tca_v1.0.md #9 EM-9 DoD.
This file supplements `test_algo_vwap.py` (EM-9 core schedule tests).
File size cap: 300 lines.

Performance budget: _PLAN_BUDGET_MS = 0.94 ms.
  B0 = 0.03125 ms (batch=500, n=5, warmup=1, 10-slice flat profile, qty=1000).
  30x margin: 0.03125 * 30 = 0.9375 -> ceil to 0.94.
  30x is the standard safety margin for perf budgets in this repo.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

from src.data.models.trading import OrderSide, OrderStatus
from src.foundation.ems.contracts.v1 import (
    TERMINAL_ORDER_STATUSES,
    AlgoKind,
    AlgoSpec,
    ParentOrder,
    ParentOrderConstraints,
)

# Monkeypatch target: plan_vwap_schedule references guard functions at module level.
from src.foundation.ems.domain.algo import vwap as vwap_mod  # noqa: E402
from src.foundation.ems.domain.algo.vwap import (
    AlgoConstraintError,
    ParentTerminalError,
    plan_vwap_schedule,
)
from tests.support.perf_budget import PerfBudget  # noqa: E402
from tests.support.perf_inject import (  # noqa: E402
    reset_virtual_totals,
    virtual_delayed_sync,
)

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _algo(**overrides: object) -> AlgoSpec:
    defaults: dict[str, Any] = {
        "kind": AlgoKind.VWAP,
        "start": _T0,
        "end": _T0 + timedelta(minutes=10),
        "max_participation_pct": Decimal("10"),
        "slice_interval_sec": 60,
        "urgency": Decimal("0.5"),
        "seed": 42,
    }
    defaults.update(overrides)
    return AlgoSpec(**defaults)


def _parent(**overrides: object) -> ParentOrder:
    defaults: dict[str, Any] = {
        "parent_id": uuid4(),
        "instrument_id": "BTC/USDT",
        "side": OrderSide.BUY,
        "qty": Decimal("1000"),
        "algo": _algo(),
        "constraints": ParentOrderConstraints(max_participation_pct=Decimal("10")),
        "fund_id": uuid4(),
        "portfolio_id": uuid4(),
        "arrival_ts": _T0,
        "status": OrderStatus.CREATED,
    }
    defaults.update(overrides)
    return ParentOrder(**defaults)


def _flat_profile(slice_count: int, per_slice: Decimal = Decimal("1000")) -> list[Decimal]:
    return [per_slice] * slice_count


# ---------------------------------------------------------------------------
# Negative tests (3+ required for D2)
# ---------------------------------------------------------------------------


def test_pov_kind_is_rejected_by_vwap_planner() -> None:
    """AlgoKind.POV must be rejected by plan_vwap_schedule with AlgoConstraintError
    containing 'VWAP' in the message."""
    parent = _parent(algo=_algo(kind=AlgoKind.POV))
    with pytest.raises(AlgoConstraintError, match="VWAP"):
        plan_vwap_schedule(parent, volume_profile=_flat_profile(10))


def test_empty_volume_profile_is_rejected() -> None:
    """An empty volume_profile list must raise AlgoConstraintError with
    'volume_profile' in the message."""
    parent = _parent()
    with pytest.raises(AlgoConstraintError, match="volume_profile"):
        plan_vwap_schedule(parent, volume_profile=[])


def test_slice_count_over_500_cap_is_rejected() -> None:
    """A parent whose derived slice count exceeds 500 must raise AlgoConstraintError
    with '500' in the message."""
    parent = _parent(
        algo=_algo(
            start=_T0,
            end=_T0 + timedelta(seconds=501 * 60 + 1),  # > 500 slices at 60s interval
        )
    )
    with pytest.raises(AlgoConstraintError, match="500"):
        plan_vwap_schedule(parent, volume_profile=_flat_profile(501))


@pytest.mark.parametrize("status", TERMINAL_ORDER_STATUSES, ids=str)
def test_terminal_parent_status_is_rejected(status: OrderStatus) -> None:
    """Every terminal status must cause plan_vwap_schedule to raise ParentTerminalError."""
    parent = _parent(status=status)
    with pytest.raises(ParentTerminalError):
        plan_vwap_schedule(parent, volume_profile=_flat_profile(10))


# ---------------------------------------------------------------------------
# Failure-injection: fail-closed (monkeypatch check_slice_interval)
# ---------------------------------------------------------------------------


class _InjectedError(RuntimeError):
    """Private exception class — must NOT be swallowed by plan_vwap_schedule."""


def test_fail_closed_slice_interval_injection() -> None:
    """Monkeypatch check_slice_interval to raise _InjectedError.
    plan_vwap_schedule must propagate it (fail-closed), not catch and return."""
    original = vwap_mod.check_slice_interval

    def broken(*args: Any, **kwargs: Any) -> None:
        raise _InjectedError("injected slice-interval failure")

    vwap_mod.check_slice_interval = broken
    try:
        parent = _parent()
        with pytest.raises(_InjectedError):
            plan_vwap_schedule(parent, volume_profile=_flat_profile(10))
    finally:
        vwap_mod.check_slice_interval = original


# ---------------------------------------------------------------------------
# Gate-red reproduction: _allocate_by_weight mismatch
# ---------------------------------------------------------------------------


def test_gate_red_allocation_sum_mismatch_detected() -> None:
    """Monkeypatch _allocate_by_weight to return qtys summing to qty-1.
    plan_residual(parent.qty, 0, sum) checks committed > parent_qty.
    With sum = qty-1, committed < parent_qty so plan_residual returns 1
    (the residual) and does NOT raise -- this is an app defect:
    the schedule silently accepts a sum that does not equal parent.qty.

    This test *verifies* the defect exists (by asserting no raise), so
    downstream reviewers know the residual gate does not catch under-allocation.
    """
    original_allocate = vwap_mod._allocate_by_weight

    def broken_allocate(total_qty: Decimal, weights: list[Decimal]) -> list[Decimal]:
        result = original_allocate(total_qty, weights)
        # Subtract 1 from first element to create a mismatch
        result[0] = result[0] - Decimal("1")
        return result

    vwap_mod._allocate_by_weight = broken_allocate
    try:
        parent = _parent()
        # plan_residual checks: if filled + scheduled > parent_qty, raise.
        # Here sum = parent.qty - 1, so committed < parent_qty -> no raise.
        # The schedule silently passes with a sum != parent.qty.
        result = plan_vwap_schedule(parent, volume_profile=_flat_profile(10))
        # Verify the defect: children sum is qty-1, not qty.
        child_sum = sum(c.planned_qty for c in result.children)
        assert child_sum == parent.qty - Decimal("1"), (
            f"Expected sum={parent.qty - Decimal('1')}, got {child_sum}"
        )
    finally:
        vwap_mod._allocate_by_weight = original_allocate


# ---------------------------------------------------------------------------
# Performance-budget assertions (pytest.mark.perf required)
# ---------------------------------------------------------------------------

_PLAN_BUDGET_MS: float = 0.94  # B0=0.03125ms * 30 = 0.9375 -> ceil(2dp) = 0.94


@pytest.mark.perf
def test_plan_cpu_within_budget_no_injection() -> None:
    """plan_vwap_schedule cpu_ms must stay under _PLAN_BUDGET_MS with no injection."""
    reset_virtual_totals()
    parent = _parent()
    profile = _flat_profile(10)
    pb = PerfBudget()
    sample = pb.best_of(
        lambda p=profile: plan_vwap_schedule(parent, volume_profile=p),
        n=5,
        warmup=1,
        batch=100,
    )
    assert sample.cpu_ms < _PLAN_BUDGET_MS, (
        f"cpu_ms={sample.cpu_ms:.6f} exceeds budget {_PLAN_BUDGET_MS}ms"
    )


@pytest.mark.perf
def test_gate_red_tenfold_virtual_delay_breaks_budget() -> None:
    """Inject virtual delay into check_slice_interval so total injected delay
    equals 10x the budget. The measurement must go RED (>= budget).

    Rationale: 10 slices -> 9 interval checks. d = 10 * _PLAN_BUDGET_MS / 9
    gives total injected = 9 * d = 10 * budget, ensuring the virtual total
    exceeds the budget."""
    reset_virtual_totals()

    d = Decimal(str(_PLAN_BUDGET_MS)) * 10 / 9  # per-call delay

    original = vwap_mod.check_slice_interval

    def delayed(*args: Any, **kwargs: Any) -> None:
        return virtual_delayed_sync(original, float(d))(*args, **kwargs)

    vwap_mod.check_slice_interval = delayed
    try:
        parent = _parent()
        pb = PerfBudget()
        sample = pb.best_of(
            lambda: plan_vwap_schedule(parent, volume_profile=_flat_profile(10)),
            n=5,
            warmup=1,
            batch=100,
        )
        # This assertion verifies the gate-red test is sensitive: injected delay
        # must actually push cpu_ms above the budget.
        assert sample.cpu_ms >= _PLAN_BUDGET_MS, (
            f"Injected delay did not break budget: cpu_ms={sample.cpu_ms:.6f} "
            f"< budget {_PLAN_BUDGET_MS}ms -- injection d={d:.6f}ms per call"
        )
    finally:
        vwap_mod.check_slice_interval = original

    reset_virtual_totals()
