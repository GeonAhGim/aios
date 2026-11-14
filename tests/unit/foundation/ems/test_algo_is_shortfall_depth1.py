"""EM-11 -- `domain/algo/is_shortfall.py` D2 깊이 보강 (depth-1).

Spec: docs/specs/L4_ems_routing_algos_and_tca_v1.0.md #9 EM-11 DoD
("urgency-cost tradeoff, monotonicity, sum conservation").

보강 항목 (기존 test_algo_is_shortfall.py 의 D2 미달 항목만):
- negative 테스트 3종 이상 (urgency boundary, carry propagation, zero qty)
- failure_injection: 1 (guard.check_participation 가짜 inject → 적색)
- gate_red: 1 (corrupted max_participation_pct → 적색 확인)
- perf: 0 (기존 테스트에 이미 perf 단언 1개 있으므로 신설 불필요)

기존 테스트의 fixture·임포트·패턴을 그대로 따른다.
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
from src.foundation.ems.domain.algo.is_shortfall import (
    plan_is_schedule,
)
from tests.support.perf_budget import PerfBudget

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


# -- negative tests (3종 이상) -----------------------------------------------

# (n-1) urgency=1 boundary: last slice must be strictly smallest, not zero


def test_urgency_one_last_slice_is_nonzero_with_abundant_volume() -> None:
    """urgency=1 front-loads but every slice still gets a positive share
    when volume is abundant; the last slice is [1/10] of qty, not 0."""
    parent = _parent(
        qty=Decimal("1000"),
        algo=_algo(urgency=Decimal("1"), max_participation_pct=Decimal("100")),
    )
    children = plan_is_schedule(parent, volume_profile=_abundant_volume_profile(4))
    quantities = [c.planned_qty for c in children]
    assert quantities[-1] > Decimal("0")
    assert quantities == [Decimal("400"), Decimal("300"), Decimal("200"), Decimal("100")]


# (n-2) carry propagation: tightly capped early slices push all shortfall to tail


def test_carry_propagates_through_multiple_capped_slices() -> None:
    """When the first three slices are all hard-capped by participation,
    the carry accumulator grows and the final slice absorbs the remainder.
    Verifies the carry loop does not overflow or lose quantity."""
    parent = _parent(
        qty=Decimal("10000"),
        algo=_algo(urgency=Decimal("1"), max_participation_pct=Decimal("1")),
    )
    # 1% of 100 = 1 per slice cap
    tight_profile = [Decimal("100")] * 4
    children = plan_is_schedule(parent, volume_profile=tight_profile)
    quantities = [c.planned_qty for c in children]
    # Each non-final slice is capped at 1; tail absorbs everything else
    assert quantities[0] <= Decimal("1")
    assert quantities[1] <= Decimal("1")
    assert quantities[2] <= Decimal("1")
    assert sum(quantities) == Decimal("10000")


# (n-3) urgency=0 with tight participation cap: still sums exactly


def test_urgency_zero_with_tight_cap_still_conserves() -> None:
    """urgency=0 is equal-split TWAP-like. With a tight cap, the tail
    absorbs the shortfall and conservation still holds."""
    parent = _parent(
        qty=Decimal("1000"),
        algo=_algo(urgency=Decimal("0"), max_participation_pct=Decimal("5")),
    )
    tight_profile = [Decimal("1000")] * 4
    children = plan_is_schedule(parent, volume_profile=tight_profile)
    assert sum(c.planned_qty for c in children) == Decimal("1000")
    # Equal split would be 250 each, but 5% of 1000 = 50 cap
    assert all(c.planned_qty <= Decimal("50") for c in children[:-1])


# (n-4) zero urgency with zero-urgency edge: all slices equal


def test_urgency_zero_produces_equal_slices() -> None:
    """urgency=0 must produce exactly equal quantities per slice.
    Uses a 4-slice window (240s / 60s) matching the default _algo config."""
    parent = _parent(
        qty=Decimal("500"),
        algo=_algo(urgency=Decimal("0"), max_participation_pct=Decimal("100")),
    )
    children = plan_is_schedule(parent, volume_profile=_abundant_volume_profile(4))
    quantities = [c.planned_qty for c in children]
    assert len(quantities) == 4
    assert all(q == Decimal("125") for q in quantities)


# -- failure_injection: 1 -----------------------------------------------------

# (f-1) inject a fake guard.check_participation that always raises


def test_injected_guard_always_raises_participation_exceeded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Inject a fake `guard.check_participation` that unconditionally raises
    ParticipationExceededError. The planner should propagate this error
    (fail-closed: never swallow domain violations)."""
    from src.foundation.ems.domain.algo.guard import ParticipationExceededError

    def fake_check_participation(*args: Any, **kwargs: Any) -> None:
        raise ParticipationExceededError("injected failure: always exceeds cap")

    monkeypatch.setattr(
        "src.foundation.ems.domain.algo.is_shortfall.check_participation",
        fake_check_participation,
    )
    parent = _parent(
        qty=Decimal("1000"),
        algo=_algo(max_participation_pct=Decimal("100")),
    )
    with pytest.raises(ParticipationExceededError, match="injected failure"):
        plan_is_schedule(parent, volume_profile=_abundant_volume_profile(4))


# -- gate_red: corrupted max_participation_pct passes silently without guard --

# (g-1) without _validate_participation_cap, a negative cap slips through


def test_gate_red_negative_participation_cap_passes_without_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Remove `_validate_participation_cap`'s upfront call: a negative
    `max_participation_pct` should slip through to the per-slice loop and
    crash (or produce nonsensical results). This proves the upfront
    validation is necessary for fail-closed behavior.

    Uses `object.__setattr__` to bypass Pydantic validation on AlgoSpec,
    matching the pattern in the existing gate-red test.
    """
    import src.foundation.ems.domain.algo.is_shortfall as is_module

    # Patch away the validation so the corrupted cap passes silently
    monkeypatch.setattr(is_module, "_validate_participation_cap", lambda cap: None)

    parent = _parent(
        qty=Decimal("1000"),
        algo=_algo(max_participation_pct=Decimal("10")),
    )
    # Corrupt the cap after construction (bypasses Pydantic)
    object.__setattr__(parent.algo, "max_participation_pct", Decimal("-5"))
    # Without validation, this should either crash or produce garbage
    children = plan_is_schedule(parent, volume_profile=_abundant_volume_profile(4))
    # If we reach here, the negative cap was not caught -- check result is nonsensical
    # A negative cap: cap_qty = 100000 * (-5) / 100 = -50000
    # max(0, -50000) = 0, so all non-final slices get 0 and tail gets everything
    assert children[-1].planned_qty == Decimal("1000")


# -- perf: local measurement at 500 slices (budget: 25ms, best-of-15) --------

# (p-1) 500-slice performance stays under budget


@pytest.mark.perf
def test_plan_is_schedule_500_slices_under_budget(
    perf_budget: PerfBudget,
) -> None:
    """One ``plan_is_schedule`` call at the 500-slice cap must stay far
    under 25ms (best-of-15). The budget is ~10x the local single-call CPU
    time measured on this host. Clears virtual-delay ledger first so
    earlier tests cannot inflate the reading (task-22280)."""
    from tests.support.perf_inject import reset_virtual_totals

    reset_virtual_totals()
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

    children = plan_is_schedule(parent, volume_profile=profile)
    assert len(children) == 500
    assert abs(sum(c.planned_qty for c in children) - Decimal("1000000")) < Decimal("0.000001")

    perf_budget.assert_within(
        lambda: plan_is_schedule(parent, volume_profile=profile),
        budget_ms=25.0,
        n=15,
        label="plan_is_schedule 500-slice depth1",
    )
