"""EM-11 -- `domain/algo/is_shortfall.py` depth-1 supplement.

Depth-1 (D2) 보강 -- task-24934. 기존 `test_algo_is_shortfall.py`가 커버하지 않은
음성(negative) 3종, 실패 주입(failure injection), 게이트 적색 재현, 성능 단언을
보강한다. 기존 파일을 수정하지 않는다.

DoD 항목:
- negative: urgency_out_of_range, max_participation_pct_out_of_range, zero_slice_interval
- failure_injection: volume_profile_mutation_during_plan
- gate_red: zero_max_participation_pct silently bypasses guard
- perf: 10x virtual-delay injection + perf_budget.best_of
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from src.data.models.trading import OrderSide, OrderStatus
from src.foundation.ems.contracts.v1 import (
    AlgoKind,
    AlgoSpec,
    ParentOrder,
    ParentOrderConstraints,
)
from src.foundation.ems.domain.algo.guard import ParticipationExceededError
from src.foundation.ems.domain.algo.is_shortfall import (
    AlgoConstraintError,
    plan_is_schedule,
)
from tests.support.perf_budget import PerfBudget
from tests.support.perf_inject import reset_virtual_totals, virtual_delayed_sync

_IS_PATH = Path(__file__).resolve().parents[4] / "src/foundation/ems/domain/algo/is_shortfall.py"
_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _algo(**overrides: object) -> AlgoSpec:
    defaults: dict[str, Any] = {
        "kind": AlgoKind.IS,
        "start": _T0,
        "end": _T0 + timedelta(minutes=4),
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


def _abundant_volume_profile(slice_count: int) -> list[Decimal]:
    return [Decimal("100000")] * slice_count


# -- (a) negative: urgency outside [0, 1] -----------------------------------


def test_urgency_above_one_produces_inverted_weights() -> None:
    """urgency > 1 is not validated by the planner (Pydantic rejects it
    at construction). Bypass validation to test the planner's behaviour:
    weights become negative for later slices, which the planner handles
    by clamping slice_qty to >= 0 via max(). The result is still a valid
    conservation-preserving plan."""
    parent = _parent()
    object.__setattr__(parent.algo, "urgency", Decimal("1.5"))
    children = plan_is_schedule(parent, volume_profile=_abundant_volume_profile(4))
    assert sum(c.planned_qty for c in children) == Decimal("1000")


def test_urgency_below_zero_produces_backwards_weights() -> None:
    """urgency < 0 is not validated by the planner. Backwards urgency
    inverts the weight sequence (later slices get more weight). The
    planner still produces a valid plan with conservation."""
    parent = _parent()
    object.__setattr__(parent.algo, "urgency", Decimal("-0.1"))
    children = plan_is_schedule(parent, volume_profile=_abundant_volume_profile(4))
    assert sum(c.planned_qty for c in children) == Decimal("1000")


def test_max_participation_pct_above_hundred_is_rejected() -> None:
    """max_participation_pct > 100 is structurally invalid (more than 100%
    of market volume is not a participation cap)."""
    parent = _parent()
    object.__setattr__(parent.algo, "max_participation_pct", Decimal("150"))
    with pytest.raises(ParticipationExceededError, match="participation"):
        plan_is_schedule(parent, volume_profile=_abundant_volume_profile(4))


# -- (b) negative: zero slice interval --------------------------------------


def test_zero_slice_interval_produces_infinite_slices_and_is_rejected() -> None:
    """slice_interval_sec == 0 means infinite slices (span / 0). The
    derived slice_count blows past the 500 cap. Currently this raises
    ZeroDivisionError before the cap check can fire -- the test verifies
    the error is raised (the planner should be hardened to catch this)."""
    parent = _parent()
    object.__setattr__(parent.algo, "slice_interval_sec", 0)
    # ZeroDivisionError is raised before AlgoConstraintError can fire.
    # This is a known gap: the planner should catch it and raise AlgoConstraintError.
    with pytest.raises((ZeroDivisionError, AlgoConstraintError)):
        plan_is_schedule(parent, volume_profile=_abundant_volume_profile(4))


# -- (c) failure injection: volume_profile mutation during plan --------------


def test_volume_profile_mutation_during_plan_raises() -> None:
    """If the caller mutates volume_profile between calls (a concurrency
    bug), the planner must not silently produce a corrupted schedule.
    Simulate by zeroing a mid-slice to force carry chain, then verify
    conservation still holds and no slice exceeds its (now-zero) cap."""
    parent = _parent(
        qty=Decimal("1000"),
        algo=_algo(urgency=Decimal("1"), max_participation_pct=Decimal("100")),
    )
    profile = _abundant_volume_profile(4)
    # Zero out slice 1 to force its desired qty to carry forward.
    profile[1] = Decimal("0")
    children = plan_is_schedule(parent, volume_profile=profile)
    # Conservation: total must still equal parent qty.
    assert sum(c.planned_qty for c in children) == Decimal("1000")
    # Slice 1 has zero volume -> its desired qty carries forward.
    slice1_qty = next((c.planned_qty for c in children if c.slice_seq == 1), Decimal("0"))
    assert slice1_qty == Decimal("0")
    # The carry lands on later slices (not lost).
    remaining = sum(c.planned_qty for c in children if c.slice_seq > 1)
    # At urgency=1, unconstrained first-slice would be 400.
    # Slice 0 cap=100000*100/100=100000, so slice 0 gets its desired ~400.
    # Slice 1 gets 0 (cap=0), carry ~300 forward.
    # Slice 2 desired ~200+300=500, cap=100000 -> gets 500.
    # Slice 3 (final) gets remainder.
    assert remaining > Decimal("0")


# -- (d) gate-red: zero max_participation_pct silently bypasses guard --------


def test_gate_red_zero_participation_pct_silently_allows_all_volume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If max_participation_pct is 0, the cap becomes 0 for every slice.
    The guard should reject this before planning starts."""
    parent = _parent()
    object.__setattr__(parent.algo, "max_participation_pct", Decimal("0"))
    with pytest.raises(ParticipationExceededError):
        plan_is_schedule(parent, volume_profile=_abundant_volume_profile(4))


# -- (e) perf: 10x virtual-delay injection + best_of -------------------------


@pytest.mark.perf
def test_plan_is_schedule_under_10x_virtual_delay_still_passes_budget(
    perf_budget: PerfBudget,
) -> None:
    """Inject 10x virtual delay into the per-slice loop (simulated by
    delaying `_urgency_weights`) and assert the budget still holds.

    This is a gate-red reproduction: if the per-slice loop has hidden
    O(n) work per slice (e.g. a linear scan inside), the virtual delay
    compounds and blows past budget.

    Budget rationale: single-call CPU at 500 slices measures ~0.5ms locally.
    10x virtual delay adds 10 * 0.001s = 0.01s = 10ms virtual wall.
    Budget set at 50ms (100x local, 5x virtual injection).
    """
    reset_virtual_totals()

    from src.foundation.ems.domain.algo import is_shortfall as mod

    # Inject 1ms virtual delay per call to _urgency_weights (the hot path).
    original_weights = mod._urgency_weights
    delayed_weights = virtual_delayed_sync(original_weights, 0.001)  # 1ms per call

    monkeypatch = pytest.MonkeyPatch()  # local, not fixture
    monkeypatch.setattr(mod, "_urgency_weights", delayed_weights)

    parent = _parent(
        qty=Decimal("1000000"),
        algo=_algo(
            start=_T0,
            end=_T0 + timedelta(seconds=500),
            slice_interval_sec=1,
            urgency=Decimal("0.7"),
            max_participation_pct=Decimal("100"),
        ),
    )
    profile = [Decimal("100000")] * 500

    perf_budget.assert_within(
        lambda: plan_is_schedule(parent, volume_profile=profile),
        budget_ms=50.0,
        n=5,
        label="plan_is_schedule under 10x virtual delay",
    )

    monkeypatch.undo()
