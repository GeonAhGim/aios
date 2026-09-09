"""EM-8 -- `domain/algo/twap.py` unit tests.

Spec: docs/specs/L4_ems_routing_algos_and_tca_v1.0.md #9 EM-8 DoD
("schedule snapshot, determinism"). No DB -- pure function tests only.
"""
from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
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
from src.foundation.ems.domain.algo.twap import (
    AlgoConstraintError,
    ParentTerminalError,
    plan_twap_schedule,
)

_TWAP_PATH = Path(__file__).resolve().parents[4] / "src/foundation/ems/domain/algo/twap.py"
_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _algo(**overrides: object) -> AlgoSpec:
    defaults: dict[str, object] = {
        "kind": AlgoKind.TWAP,
        "start": _T0,
        "end": _T0 + timedelta(minutes=10),
        "max_participation_pct": Decimal("10"),
        "slice_interval_sec": 60,
        "urgency": Decimal("0.5"),
        "seed": 42,
    }
    defaults.update(overrides)
    return AlgoSpec(**defaults)  # type: ignore[arg-type]


def _parent(**overrides: object) -> ParentOrder:
    defaults: dict[str, object] = {
        "parent_id": uuid4(),
        "instrument_id": "BTC/USDT",
        "side": OrderSide.BUY,
        "qty": Decimal("10"),
        "algo": _algo(),
        "constraints": ParentOrderConstraints(max_participation_pct=Decimal("10")),
        "fund_id": uuid4(),
        "portfolio_id": uuid4(),
        "arrival_ts": _T0,
        "status": OrderStatus.CREATED,
    }
    defaults.update(overrides)
    return ParentOrder(**defaults)  # type: ignore[arg-type]


def _abundant_volume_profile(slice_count: int) -> list[Decimal]:
    return [Decimal("1000000")] * slice_count


# -- schedule snapshot: count, sum, window -----------------------------------


def test_slice_count_derived_from_window_and_interval() -> None:
    parent = _parent(algo=_algo(start=_T0, end=_T0 + timedelta(minutes=10), slice_interval_sec=60))
    children = plan_twap_schedule(parent, volume_profile=_abundant_volume_profile(10))
    assert len(children) == 10


def test_children_sum_exactly_to_parent_qty() -> None:
    parent = _parent(qty=Decimal("7"), algo=_algo(slice_interval_sec=90))
    slice_count = 10 * 60 // 90  # matches _slice_count's floor derivation
    children = plan_twap_schedule(parent, volume_profile=_abundant_volume_profile(slice_count))
    assert sum(c.planned_qty for c in children) == parent.qty


def test_all_scheduled_times_within_window() -> None:
    parent = _parent()
    children = plan_twap_schedule(parent, volume_profile=_abundant_volume_profile(10))
    for child in children:
        assert parent.algo.start <= child.scheduled_at <= parent.algo.end


def test_slice_gaps_never_shorter_than_slice_interval_sec() -> None:
    parent = _parent(algo=_algo(slice_interval_sec=60))
    children = plan_twap_schedule(parent, volume_profile=_abundant_volume_profile(10))
    for prev, nxt in zip(children, children[1:]):  # noqa: B905 -- pairwise, lengths differ by design
        gap = (nxt.scheduled_at - prev.scheduled_at).total_seconds()
        assert gap >= 60


def test_single_slice_when_window_shorter_than_interval() -> None:
    parent = _parent(algo=_algo(start=_T0, end=_T0 + timedelta(seconds=30), slice_interval_sec=60))
    children = plan_twap_schedule(parent, volume_profile=_abundant_volume_profile(1))
    assert len(children) == 1
    assert children[0].planned_qty == parent.qty


# -- determinism (EM-A3) ------------------------------------------------------


def test_same_inputs_produce_an_identical_plan() -> None:
    parent = _parent()
    profile = _abundant_volume_profile(10)
    first = plan_twap_schedule(parent, volume_profile=profile)
    second = plan_twap_schedule(parent, volume_profile=profile)
    assert first == second


def test_different_parent_id_changes_child_id_but_not_quantities_or_times() -> None:
    profile = _abundant_volume_profile(10)
    parent_a = _parent(parent_id=uuid4())
    parent_b = _parent(parent_id=uuid4(), algo=parent_a.algo, qty=parent_a.qty)
    children_a = plan_twap_schedule(parent_a, volume_profile=profile)
    children_b = plan_twap_schedule(parent_b, volume_profile=profile)
    assert [c.child_id for c in children_a] != [c.child_id for c in children_b]
    assert [c.planned_qty for c in children_a] == [c.planned_qty for c in children_b]
    assert [c.scheduled_at for c in children_a] == [c.scheduled_at for c in children_b]


# -- EM-A4: terminal parent never gets new children --------------------------


@pytest.mark.parametrize("terminal_status", sorted(TERMINAL_ORDER_STATUSES, key=str))
def test_terminal_parent_is_rejected(terminal_status: OrderStatus) -> None:
    parent = _parent(status=terminal_status)
    with pytest.raises(ParentTerminalError, match="terminal"):
        plan_twap_schedule(parent, volume_profile=_abundant_volume_profile(10))


def test_non_terminal_parent_is_accepted() -> None:
    parent = _parent(status=OrderStatus.SUBMITTED)
    children = plan_twap_schedule(parent, volume_profile=_abundant_volume_profile(10))
    assert len(children) == 10


# -- kind guard: this module only plans TWAP ---------------------------------


def test_non_twap_kind_is_rejected() -> None:
    parent = _parent(algo=_algo(kind=AlgoKind.VWAP))
    with pytest.raises(AlgoConstraintError, match="TWAP"):
        plan_twap_schedule(parent, volume_profile=_abundant_volume_profile(10))


# -- fail-closed volume_profile shape -----------------------------------------


def test_short_volume_profile_is_rejected() -> None:
    parent = _parent()
    with pytest.raises(AlgoConstraintError, match="volume_profile"):
        plan_twap_schedule(parent, volume_profile=_abundant_volume_profile(9))


def test_long_volume_profile_is_rejected() -> None:
    parent = _parent()
    with pytest.raises(AlgoConstraintError, match="volume_profile"):
        plan_twap_schedule(parent, volume_profile=_abundant_volume_profile(11))


def test_slice_count_over_the_cap_is_rejected() -> None:
    parent = _parent(
        algo=_algo(start=_T0, end=_T0 + timedelta(seconds=1000), slice_interval_sec=1)
    )
    with pytest.raises(AlgoConstraintError, match="500"):
        plan_twap_schedule(parent, volume_profile=_abundant_volume_profile(1000))


# -- EM-A2 participation cap is authoritatively enforced by guard.py ---------


def test_participation_cap_violation_on_a_non_final_slice_is_rejected() -> None:
    """qty is split evenly (10 slices of 1 each); a cap of 1% against a
    market volume of 10 allows at most 0.1 per slice -- every non-final
    slice violates it, proving guard.check_participation actually runs
    (this port passes `volume_profile=None` to the legacy OMS slicer, so
    nothing here is pre-softened -- the rejection can only come from the
    guard call)."""
    parent = _parent(qty=Decimal("10"), algo=_algo(max_participation_pct=Decimal("1")))
    tight_profile = [Decimal("10")] * 10
    with pytest.raises(ParticipationExceededError, match="exceeding the cap"):
        plan_twap_schedule(parent, volume_profile=tight_profile)


def test_final_slice_is_exempt_from_the_participation_cap() -> None:
    """A cap tight enough to reject every non-final slice's share must
    still let a *single*-slice schedule through, because that one slice
    is always the close-out tail (EM-A1 overrides EM-A2 on the tail)."""
    parent = _parent(
        qty=Decimal("10"),
        algo=_algo(
            start=_T0,
            end=_T0 + timedelta(seconds=30),
            slice_interval_sec=60,
            max_participation_pct=Decimal("1"),
        ),
    )
    children = plan_twap_schedule(parent, volume_profile=[Decimal("1")])
    assert len(children) == 1
    assert children[0].planned_qty == parent.qty


# -- zero-quantity slices are filtered, never sent to ChildOrder gt=0 --------


def test_zero_quantity_slices_are_filtered_out_of_the_result() -> None:
    parent = _parent(qty=Decimal("0.00000002"), algo=_algo(slice_interval_sec=200))
    children = plan_twap_schedule(parent, volume_profile=_abundant_volume_profile(3))
    assert all(c.planned_qty > 0 for c in children)
    assert sum(c.planned_qty for c in children) == parent.qty
    assert len(children) < 3


# -- EM-A1 residual guard is actually wired (I-10) ---------------------------


def _imported_top_level_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            found.add(node.module)
    return found


def test_twap_module_imports_all_three_guard_functions() -> None:
    imported = _imported_top_level_modules(_TWAP_PATH)
    assert any("domain.algo.guard" in module for module in imported), (
        "twap.py must delegate participation/interval/residual checks to "
        "EM-7's guard.py -- see plan_twap_schedule."
    )


def test_twap_module_calls_all_three_guard_functions() -> None:
    source = _TWAP_PATH.read_text(encoding="utf-8")
    for guard_call in ("check_participation(", "check_slice_interval(", "plan_residual("):
        assert guard_call in source, f"twap.py must call guard.{guard_call} -- wiring regressed."


def test_twap_module_does_not_reimplement_oms_algo_slicer() -> None:
    imported = _imported_top_level_modules(_TWAP_PATH)
    assert any("oms.domain.algo_slicer" in module for module in imported), (
        "EM-8 promotes the existing oms/domain/algo_slicer.py (no rewrite) -- "
        "twap.py must import plan_slices from it rather than reimplementing "
        "slicing math."
    )


# -- file size discipline -----------------------------------------------------


def test_twap_module_is_at_most_300_lines() -> None:
    line_count = len(_TWAP_PATH.read_text(encoding="utf-8").splitlines())
    assert line_count <= 300, f"twap.py has {line_count} lines, exceeding the 300-line leaf cap."
