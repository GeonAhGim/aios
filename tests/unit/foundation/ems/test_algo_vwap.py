"""EM-9 -- `domain/algo/vwap.py` unit tests.

Spec: docs/specs/L4_ems_routing_algos_and_tca_v1.0.md #9 EM-9 DoD
("volume profile based"), #10 (shallow-profile -> TWAP demotion, reason
recorded). No DB -- pure function tests only.
"""
from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
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
from src.foundation.ems.domain.algo.vwap import (
    AlgoConstraintError,
    ParentTerminalError,
    plan_vwap_schedule,
)

_VWAP_PATH = Path(__file__).resolve().parents[4] / "src/foundation/ems/domain/algo/vwap.py"
_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


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


# -- schedule snapshot: weighted allocation, sum, window ----------------------


def test_children_sum_exactly_to_parent_qty() -> None:
    """volume_profile is scaled well above the 10%-cap floor
    (qty / total_weight * 100 <= max_participation_pct must hold for every
    non-final slice, since a non-demoted slice's own weight is also the
    market volume its participation is checked against) so the shape test
    is not entangled with a cap rejection."""
    parent = _parent(qty=Decimal("777"))
    profile = [Decimal(v) for v in (1000, 2000, 3000, 500, 600, 700, 800, 900, 400, 300)]
    children = plan_vwap_schedule(parent, volume_profile=profile).children
    assert sum(c.planned_qty for c in children) == parent.qty


def test_slice_share_is_proportional_to_its_volume_profile_weight() -> None:
    """slice 1 has 3x slice 0's volume -- with an abundant tail slice too
    (so neither is the remainder-absorbing last slice), its planned qty
    must be exactly 3x."""
    parent = _parent(qty=Decimal("1200"))
    profile = [Decimal("1000"), Decimal("3000")] + [Decimal("1000")] * 8
    children = plan_vwap_schedule(parent, volume_profile=profile).children
    assert children[1].planned_qty == children[0].planned_qty * 3


def test_all_scheduled_times_within_window() -> None:
    parent = _parent()
    children = plan_vwap_schedule(parent, volume_profile=_flat_profile(10)).children
    for child in children:
        assert parent.algo.start <= child.scheduled_at <= parent.algo.end


def test_slice_gaps_never_shorter_than_slice_interval_sec() -> None:
    parent = _parent(algo=_algo(slice_interval_sec=60))
    children = plan_vwap_schedule(parent, volume_profile=_flat_profile(10)).children
    for prev, nxt in zip(children, children[1:]):  # noqa: B905 -- pairwise, lengths differ by design
        gap = (nxt.scheduled_at - prev.scheduled_at).total_seconds()
        assert gap >= 60


# -- determinism (EM-A3) --------------------------------------------------------


def test_same_inputs_produce_an_identical_plan() -> None:
    parent = _parent()
    profile = _flat_profile(10)
    first = plan_vwap_schedule(parent, volume_profile=profile)
    second = plan_vwap_schedule(parent, volume_profile=profile)
    assert first.children == second.children
    assert first.demoted_to_twap == second.demoted_to_twap


def test_different_parent_id_changes_child_id_but_not_quantities_or_times() -> None:
    profile = _flat_profile(10)
    parent_a = _parent(parent_id=uuid4())
    parent_b = _parent(parent_id=uuid4(), algo=parent_a.algo, qty=parent_a.qty)
    children_a = plan_vwap_schedule(parent_a, volume_profile=profile).children
    children_b = plan_vwap_schedule(parent_b, volume_profile=profile).children
    assert [c.child_id for c in children_a] != [c.child_id for c in children_b]
    assert [c.planned_qty for c in children_a] == [c.planned_qty for c in children_b]
    assert [c.scheduled_at for c in children_a] == [c.scheduled_at for c in children_b]


# -- EM-A4: terminal parent never gets new children ----------------------------


@pytest.mark.parametrize("terminal_status", sorted(TERMINAL_ORDER_STATUSES, key=str))
def test_terminal_parent_is_rejected(terminal_status: OrderStatus) -> None:
    parent = _parent(status=terminal_status)
    with pytest.raises(ParentTerminalError, match="terminal"):
        plan_vwap_schedule(parent, volume_profile=_flat_profile(10))


def test_non_terminal_parent_is_accepted() -> None:
    parent = _parent(status=OrderStatus.SUBMITTED)
    result = plan_vwap_schedule(parent, volume_profile=_flat_profile(10))
    assert len(result.children) == 10


# -- kind guard: this module only plans VWAP -----------------------------------


def test_non_vwap_kind_is_rejected() -> None:
    parent = _parent(algo=_algo(kind=AlgoKind.TWAP))
    with pytest.raises(AlgoConstraintError, match="VWAP"):
        plan_vwap_schedule(parent, volume_profile=_flat_profile(10))


# -- fail-closed volume_profile shape ------------------------------------------


def test_short_volume_profile_is_rejected() -> None:
    parent = _parent()
    with pytest.raises(AlgoConstraintError, match="volume_profile"):
        plan_vwap_schedule(parent, volume_profile=_flat_profile(9))


def test_long_volume_profile_is_rejected() -> None:
    parent = _parent()
    with pytest.raises(AlgoConstraintError, match="volume_profile"):
        plan_vwap_schedule(parent, volume_profile=_flat_profile(11))


def test_slice_count_over_the_cap_is_rejected() -> None:
    parent = _parent(
        algo=_algo(start=_T0, end=_T0 + timedelta(seconds=1000), slice_interval_sec=1)
    )
    with pytest.raises(AlgoConstraintError, match="500"):
        plan_vwap_schedule(parent, volume_profile=_flat_profile(1000))


# -- EM-A2 participation cap is authoritatively enforced by guard.py ----------


def test_participation_cap_violation_on_a_non_final_slice_is_rejected() -> None:
    """A flat profile splits qty evenly (10 slices of 1 each); a cap of 1%
    against a market volume of 10 allows at most 0.1 per slice -- every
    non-final slice violates it, proving guard.check_participation
    actually runs against the real per-slice volume_profile value."""
    parent = _parent(qty=Decimal("10"), algo=_algo(max_participation_pct=Decimal("1")))
    tight_profile = [Decimal("10")] * 10
    with pytest.raises(ParticipationExceededError, match="exceeding the cap"):
        plan_vwap_schedule(parent, volume_profile=tight_profile)


def test_final_slice_is_exempt_from_the_participation_cap() -> None:
    parent = _parent(
        qty=Decimal("10"),
        algo=_algo(
            start=_T0,
            end=_T0 + timedelta(seconds=30),
            slice_interval_sec=60,
            max_participation_pct=Decimal("1"),
        ),
    )
    children = plan_vwap_schedule(parent, volume_profile=[Decimal("1")]).children
    assert len(children) == 1
    assert children[0].planned_qty == parent.qty


# -- zero-weight slices are filtered, never sent to ChildOrder gt=0 ----------


def test_zero_weight_slice_is_filtered_out_without_raising() -> None:
    """A single zero entry among an otherwise flat profile (90% coverage,
    not demoted) gets a zero share and must be dropped from the plan
    instead of producing a `ChildOrder(planned_qty=0)` (violates the
    contract's `gt=0`) or raising."""
    profile = _flat_profile(10)
    profile[3] = Decimal("0")
    parent = _parent(qty=Decimal("900"))
    children = plan_vwap_schedule(parent, volume_profile=profile).children
    assert all(c.planned_qty > 0 for c in children)
    assert 3 not in {c.slice_seq for c in children}
    assert len(children) == 9
    assert sum(c.planned_qty for c in children) == parent.qty


# -- shallow-profile demotion to TWAP (spec #10) -------------------------------


def test_flat_profile_is_not_demoted() -> None:
    parent = _parent()
    result = plan_vwap_schedule(parent, volume_profile=_flat_profile(10))
    assert result.demoted_to_twap is False
    assert result.demotion_reason is None


def test_sparse_profile_below_coverage_floor_is_demoted_to_equal_weight() -> None:
    """6 of 10 slices are zero (40% coverage < 50% floor) -- demoted, and
    the surviving equal-weight allocation ignores the lopsided shape:
    every slice gets the same share instead of concentrating in the 4
    slices that happen to carry volume. The 4 nonzero entries are scaled
    (5000, not 500) so the demoted per-slice average (2000) keeps every
    equal-weight share (100) within the default 10% participation cap --
    demotion changes the *weighting*, not the cap, so the numbers must
    still respect it."""
    parent = _parent(qty=Decimal("1000"))
    profile = [Decimal("0")] * 6 + [Decimal("5000")] * 4
    result = plan_vwap_schedule(parent, volume_profile=profile)
    assert result.demoted_to_twap is True
    assert result.demotion_reason is not None
    assert "coverage" in result.demotion_reason
    qtys = {c.planned_qty for c in result.children[:-1]}
    assert qtys == {Decimal("100")}


def test_fully_empty_profile_fails_closed_on_the_participation_check() -> None:
    """A profile with zero total volume is demoted (coverage is 0%), but
    demotion is not an exemption from EM-A2: the substituted average
    market volume is also zero, so `guard.check_participation` still
    fail-closes on the first non-final slice -- same fail-closed rule
    EM-8's twap.py already applies when handed all-zero volume data."""
    parent = _parent(qty=Decimal("100"))
    with pytest.raises(ParticipationExceededError, match="market_volume must be > 0"):
        plan_vwap_schedule(parent, volume_profile=_flat_profile(10, Decimal("0")))


def test_demoted_participation_check_uses_average_volume_not_the_untrusted_shape() -> None:
    """Demoted (6 zero + 4 non-zero slices of 5000, average == 2000); a
    per-slice value of 0 for the first six slices would fail-close
    immediately if the raw shape were still used for the participation
    check -- using the profile's own average instead lets every non-final
    slice's equal 100-unit share (5% of 2000) pass."""
    parent = _parent(qty=Decimal("1000"), algo=_algo(max_participation_pct=Decimal("10")))
    profile = [Decimal("0")] * 6 + [Decimal("5000")] * 4
    result = plan_vwap_schedule(parent, volume_profile=profile)
    assert result.demoted_to_twap is True
    assert len(result.children) == 10


# -- EM-A1 residual guard is actually wired (I-10) ----------------------------


def _imported_top_level_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            found.add(node.module)
    return found


def test_vwap_module_imports_all_three_guard_functions() -> None:
    imported = _imported_top_level_modules(_VWAP_PATH)
    assert any("domain.algo.guard" in module for module in imported), (
        "vwap.py must delegate participation/interval/residual checks to "
        "EM-7's guard.py -- see plan_vwap_schedule."
    )


def test_vwap_module_calls_all_three_guard_functions() -> None:
    source = _VWAP_PATH.read_text(encoding="utf-8")
    for guard_call in ("check_participation(", "check_slice_interval(", "plan_residual("):
        assert guard_call in source, f"vwap.py must call guard.{guard_call} -- wiring regressed."


def test_vwap_module_does_not_recompute_the_participation_check_itself() -> None:
    source = _VWAP_PATH.read_text(encoding="utf-8")
    assert "/ market_volume" not in source


# -- file size discipline ------------------------------------------------------


def test_vwap_module_is_at_most_280_lines() -> None:
    line_count = len(_VWAP_PATH.read_text(encoding="utf-8").splitlines())
    assert line_count <= 280, f"vwap.py has {line_count} lines, exceeding the 280-line leaf cap."
