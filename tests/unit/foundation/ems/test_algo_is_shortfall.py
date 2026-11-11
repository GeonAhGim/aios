"""EM-11 -- `domain/algo/is_shortfall.py` unit tests.

Spec: docs/specs/L4_ems_routing_algos_and_tca_v1.0.md #9 EM-11 DoD
("urgency-cost tradeoff, monotonicity, sum conservation"). No DB -- pure
function tests only.
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
    TERMINAL_ORDER_STATUSES,
    AlgoKind,
    AlgoSpec,
    ParentOrder,
    ParentOrderConstraints,
)
from src.foundation.ems.domain.algo.guard import ParticipationExceededError
from src.foundation.ems.domain.algo.is_shortfall import (
    AlgoConstraintError,
    ParentTerminalError,
    plan_is_schedule,
)
from src.foundation.ems.domain.algo.twap import AlgoConstraintError as TwapAlgoConstraintError
from src.foundation.ems.domain.algo.twap import ParentTerminalError as TwapParentTerminalError
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


# -- (b) urgency=0 collapses to TWAP's equal split ----------------------------


def test_zero_urgency_splits_evenly_like_twap() -> None:
    parent = _parent(
        qty=Decimal("1000"),
        algo=_algo(urgency=Decimal("0"), max_participation_pct=Decimal("100")),
    )
    children = plan_is_schedule(parent, volume_profile=_abundant_volume_profile(4))
    assert [c.planned_qty for c in children] == [Decimal("250")] * 4


# -- (c)-1 urgency=1 front-loads: non-increasing, first > last ---------------


def test_full_urgency_front_loads_quantity_non_increasing() -> None:
    parent = _parent(
        qty=Decimal("1000"),
        algo=_algo(urgency=Decimal("1"), max_participation_pct=Decimal("100")),
    )
    children = plan_is_schedule(parent, volume_profile=_abundant_volume_profile(4))
    quantities = [c.planned_qty for c in children]
    assert quantities == [Decimal("400"), Decimal("300"), Decimal("200"), Decimal("100")]
    for current, following in zip(quantities, quantities[1:]):  # noqa: B905
        assert current >= following
    assert quantities[0] > quantities[-1]


# -- (c)-2 first-slice quantity is monotone non-decreasing in urgency --------


def test_first_slice_quantity_is_monotone_nondecreasing_in_urgency() -> None:
    urgency_grid = [Decimal("0"), Decimal("0.25"), Decimal("0.5"), Decimal("0.75"), Decimal("1")]
    first_slice_qtys = []
    for urgency in urgency_grid:
        parent = _parent(
            qty=Decimal("1000"),
            algo=_algo(urgency=urgency, max_participation_pct=Decimal("100")),
        )
        children = plan_is_schedule(parent, volume_profile=_abundant_volume_profile(4))
        first_slice_qtys.append(children[0].planned_qty)
    for lower, higher in zip(first_slice_qtys, first_slice_qtys[1:]):  # noqa: B905
        assert lower <= higher


# -- (d) sum conservation is exact for every urgency value -------------------


@pytest.mark.parametrize(
    "urgency", [Decimal("0"), Decimal("0.25"), Decimal("0.5"), Decimal("0.75"), Decimal("1")]
)
def test_planned_qty_sums_exactly_to_parent_qty_for_every_urgency(urgency: Decimal) -> None:
    parent = _parent(
        qty=Decimal("1000"),
        algo=_algo(urgency=urgency, max_participation_pct=Decimal("100")),
    )
    children = plan_is_schedule(parent, volume_profile=_abundant_volume_profile(4))
    assert sum(c.planned_qty for c in children) == Decimal("1000")


def test_sum_conservation_holds_even_when_the_participation_cap_binds() -> None:
    parent = _parent(
        qty=Decimal("1000"),
        algo=_algo(urgency=Decimal("1"), max_participation_pct=Decimal("10")),
    )
    tight_profile = [Decimal("1000")] * 4
    children = plan_is_schedule(parent, volume_profile=tight_profile)
    assert sum(c.planned_qty for c in children) == Decimal("1000")


# -- (e) participation cap is authoritative even at full urgency ------------


def test_participation_cap_binds_ahead_of_urgency_front_loading() -> None:
    """Unconstrained, urgency=1 would put 400 on the first slice (see the
    front-load test above). A 10% cap against a market volume of 1000
    limits every non-final slice to 100 -- the shortfall a slice can't
    place under the cap carries forward instead of being dropped, so it
    lands on the exempt final slice."""
    parent = _parent(
        qty=Decimal("1000"),
        algo=_algo(urgency=Decimal("1"), max_participation_pct=Decimal("10")),
    )
    tight_profile = [Decimal("1000")] * 4
    children = plan_is_schedule(parent, volume_profile=tight_profile)
    quantities = [c.planned_qty for c in children]
    assert quantities[0] <= Decimal("100")
    assert quantities == [Decimal("100"), Decimal("100"), Decimal("100"), Decimal("700")]


def test_non_final_slices_pass_the_guard_participation_check() -> None:
    from src.foundation.ems.domain.algo.guard import check_participation

    parent = _parent(
        qty=Decimal("1000"),
        algo=_algo(urgency=Decimal("1"), max_participation_pct=Decimal("10")),
    )
    tight_profile = [Decimal("1000")] * 4
    children = plan_is_schedule(parent, volume_profile=tight_profile)
    assert len(children) == len(tight_profile) == 4
    assert [child.slice_seq for child in children] == list(range(4))
    assert sum(child.planned_qty for child in children) == parent.qty
    for child, market_volume in zip(children[:-1], tight_profile[:-1], strict=True):
        assert child.planned_qty == Decimal("100")
        check_participation(child.planned_qty, market_volume, parent.algo.max_participation_pct)
        # I-07: an injected over-cap quantity must fail the real domain guard.
        with pytest.raises(ParticipationExceededError, match="exceeding the cap"):
            check_participation(
                child.planned_qty + Decimal("0.0001"),
                market_volume,
                parent.algo.max_participation_pct,
            )
    # The final residual is exempt in the planner, not in the guard itself.
    assert children[-1].planned_qty == Decimal("700")
    with pytest.raises(ParticipationExceededError, match="exceeding the cap"):
        check_participation(
            children[-1].planned_qty, tight_profile[-1], parent.algo.max_participation_pct
        )


# -- (f)-1 kind guard: this module only plans IS -----------------------------


def test_non_is_kind_is_rejected() -> None:
    parent = _parent(algo=_algo(kind=AlgoKind.TWAP))
    with pytest.raises(AlgoConstraintError, match="IS"):
        plan_is_schedule(parent, volume_profile=_abundant_volume_profile(4))


# -- (f)-2 EM-A4: terminal parent never gets new children --------------------


@pytest.mark.parametrize("terminal_status", sorted(TERMINAL_ORDER_STATUSES, key=str))
def test_terminal_parent_is_rejected(terminal_status: OrderStatus) -> None:
    parent = _parent(status=terminal_status)
    with pytest.raises(ParentTerminalError, match="terminal"):
        plan_is_schedule(parent, volume_profile=_abundant_volume_profile(4))


def test_non_terminal_parent_is_accepted() -> None:
    parent = _parent(status=OrderStatus.SUBMITTED)
    children = plan_is_schedule(parent, volume_profile=_abundant_volume_profile(4))
    assert len(children) == 4


# -- (f)-3 fail-closed volume_profile shape -----------------------------------


def test_short_volume_profile_is_rejected() -> None:
    parent = _parent()
    with pytest.raises(AlgoConstraintError, match="volume_profile"):
        plan_is_schedule(parent, volume_profile=_abundant_volume_profile(3))


def test_long_volume_profile_is_rejected() -> None:
    parent = _parent()
    with pytest.raises(AlgoConstraintError, match="volume_profile"):
        plan_is_schedule(parent, volume_profile=_abundant_volume_profile(5))


def test_slice_count_over_the_cap_is_rejected() -> None:
    parent = _parent(algo=_algo(start=_T0, end=_T0 + timedelta(seconds=1000), slice_interval_sec=1))
    with pytest.raises(AlgoConstraintError, match=r"slice_count 1000 exceeds the 500 cap"):
        plan_is_schedule(parent, volume_profile=_abundant_volume_profile(1000))


# -- rejection classes are reused from twap.py, not a new hierarchy ----------


def test_is_shortfall_reuses_twap_exception_classes() -> None:
    assert AlgoConstraintError is TwapAlgoConstraintError
    assert ParentTerminalError is TwapParentTerminalError


# -- (g) determinism (EM-A3) --------------------------------------------------


def test_same_inputs_produce_an_identical_plan() -> None:
    parent = _parent()
    profile = _abundant_volume_profile(4)
    first = plan_is_schedule(parent, volume_profile=profile)
    second = plan_is_schedule(parent, volume_profile=profile)
    assert first == second


def test_different_parent_id_changes_child_id_but_not_quantities_or_times() -> None:
    profile = _abundant_volume_profile(4)
    parent_a = _parent(parent_id=uuid4())
    parent_b = _parent(parent_id=uuid4(), algo=parent_a.algo, qty=parent_a.qty)
    children_a = plan_is_schedule(parent_a, volume_profile=profile)
    children_b = plan_is_schedule(parent_b, volume_profile=profile)
    assert [c.child_id for c in children_a] != [c.child_id for c in children_b]
    assert [c.planned_qty for c in children_a] == [c.planned_qty for c in children_b]
    assert [c.scheduled_at for c in children_a] == [c.scheduled_at for c in children_b]


# -- DEEPEN 2502 (D2): numeric performance assertion -------------------------


@pytest.mark.perf
def test_plan_is_schedule_stays_under_budget_at_the_max_slice_count(
    perf_budget: PerfBudget,
) -> None:
    """One ``plan_is_schedule`` call at 500 slices (the module's
    ``_MAX_SLICE_COUNT`` cap) must stay far under budget -- an O(n^2)
    regression in the per-slice loop, weights, or guard calls would blow
    past it. Measures a single call per sample (best of 5) so xdist CPU
    contention spikes cannot accumulate across a long sample; the budget is
    ~10x the local single-call CPU time, not inflated.

    The global virtual-delay ledger is cleared first so earlier tests in the
    same xdist worker cannot inflate ``cpu_ms`` (task-22280).
    """
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
        n=5,
        label="plan_is_schedule at 500 slices",
    )


# -- DEEPEN 2502 (D2): gate-red repro -----------------------------------------


def test_gate_red_without_the_upfront_cap_validation_the_corrupted_cap_passes_silently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Before/after proof that `_validate_participation_cap`'s call is
    load-bearing, not decorative: disable only that one guard call
    (monkeypatched to a no-op, leaving every per-slice
    `guard.check_participation` call untouched) and replay the exact
    corrupted-cap scenario from the failure-injection test above. Without
    the up-front call, nothing else in `plan_is_schedule` ever notices --
    the whole order is silently dumped into the exempt final slice."""
    from src.foundation.ems.domain.algo import is_shortfall as is_shortfall_module

    monkeypatch.setattr(is_shortfall_module, "_validate_participation_cap", lambda _pct: None)
    parent = _parent(
        qty=Decimal("1000"),
        algo=_algo(urgency=Decimal("0.5"), max_participation_pct=Decimal("10")),
    )
    object.__setattr__(parent.algo, "max_participation_pct", Decimal("-5"))
    children = plan_is_schedule(parent, volume_profile=_abundant_volume_profile(4))
    quantities = [c.planned_qty for c in children]
    assert quantities == [Decimal("1000")]
    assert sum(quantities) == Decimal("1000")


# -- schedule snapshot: window bounds, slice pacing ---------------------------


def test_all_scheduled_times_within_window() -> None:
    parent = _parent()
    children = plan_is_schedule(parent, volume_profile=_abundant_volume_profile(4))
    for child in children:
        assert parent.algo.start <= child.scheduled_at <= parent.algo.end


def test_slice_gaps_never_shorter_than_slice_interval_sec() -> None:
    parent = _parent(algo=_algo(slice_interval_sec=60))
    children = plan_is_schedule(parent, volume_profile=_abundant_volume_profile(4))
    for prev, nxt in zip(children, children[1:]):  # noqa: B905 -- pairwise, lengths differ by design
        gap = (nxt.scheduled_at - prev.scheduled_at).total_seconds()
        assert gap >= 60


# -- zero-quantity slices are filtered, never sent to ChildOrder gt=0 --------


def test_zero_volume_slice_is_filtered_out_without_raising() -> None:
    """A slice whose market volume is `0` gets a `0` participation cap
    (`guard.check_participation` fails closed on `market_volume <= 0`, so
    this module never calls it for a `0` slice_qty) -- the unmet quantity
    carries forward instead of being lost, matching (d)'s conservation."""
    profile = _abundant_volume_profile(4)
    profile[1] = Decimal("0")
    parent = _parent(
        qty=Decimal("1000"),
        algo=_algo(urgency=Decimal("0"), max_participation_pct=Decimal("100")),
    )
    children = plan_is_schedule(parent, volume_profile=profile)
    assert all(c.planned_qty > 0 for c in children)
    assert 1 not in {c.slice_seq for c in children}
    assert sum(c.planned_qty for c in children) == Decimal("1000")
