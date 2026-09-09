"""EM-10 -- `domain/algo/pov.py` unit tests.

Spec: docs/specs/L4_ems_routing_algos_and_tca_v1.0.md #9 EM-10 DoD
("real-time participation tracking"). No DB -- pure function tests only.
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
from src.foundation.ems.domain.algo.pov import (
    AlgoConstraintError,
    ParentTerminalError,
    plan_pov_schedule,
)

_POV_PATH = Path(__file__).resolve().parents[4] / "src/foundation/ems/domain/algo/pov.py"
_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _algo(**overrides: object) -> AlgoSpec:
    defaults: dict[str, Any] = {
        "kind": AlgoKind.POV,
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
        "qty": Decimal("10000"),
        "algo": _algo(),
        "constraints": ParentOrderConstraints(max_participation_pct=Decimal("10")),
        "fund_id": uuid4(),
        "portfolio_id": uuid4(),
        "arrival_ts": _T0,
        "status": OrderStatus.CREATED,
    }
    defaults.update(overrides)
    return ParentOrder(**defaults)


def _volume_profile(slice_count: int, per_slice: Decimal = Decimal("1000")) -> list[Decimal]:
    return [per_slice] * slice_count


# -- (b) participation cap numeric bound --------------------------------------


def test_planned_qty_never_exceeds_the_participation_cap_of_market_volume() -> None:
    """10 slices of 60s over a 10 minute window; max_participation_pct=10,
    volume_profile[i]=1000 -> cap_qty == 1000 * 10 / 100 == 100 per slice.
    parent.qty is abundant (10000), so every slice hits the cap exactly."""
    parent = _parent(qty=Decimal("10000"), algo=_algo(max_participation_pct=Decimal("10")))
    children = plan_pov_schedule(parent, volume_profile=_volume_profile(10, Decimal("1000")))
    assert len(children) == 10
    for child in children:
        assert child.planned_qty <= Decimal("100")
    assert children[0].planned_qty == Decimal("100")


def test_scarce_remaining_qty_never_lets_a_slice_exceed_the_cap() -> None:
    """Residual carries forward instead of being dumped past the cap: a
    parent qty smaller than 10 full-cap slices must never let an early
    slice's shortfall be recovered by exceeding the cap on a later one."""
    parent = _parent(qty=Decimal("250"), algo=_algo(max_participation_pct=Decimal("10")))
    children = plan_pov_schedule(parent, volume_profile=_volume_profile(10, Decimal("1000")))
    for child in children:
        assert child.planned_qty <= Decimal("100")
    assert sum(c.planned_qty for c in children) == Decimal("250")


# -- (c)-1: volume_profile length must match the derived slice count ---------


def test_short_volume_profile_is_rejected() -> None:
    parent = _parent()
    with pytest.raises(AlgoConstraintError, match="volume_profile"):
        plan_pov_schedule(parent, volume_profile=_volume_profile(9))


def test_long_volume_profile_is_rejected() -> None:
    parent = _parent()
    with pytest.raises(AlgoConstraintError, match="volume_profile"):
        plan_pov_schedule(parent, volume_profile=_volume_profile(11))


# -- (c)-2: terminal parent never gets new children (EM-A4) ------------------


@pytest.mark.parametrize("terminal_status", sorted(TERMINAL_ORDER_STATUSES, key=str))
def test_terminal_parent_is_rejected(terminal_status: OrderStatus) -> None:
    parent = _parent(status=terminal_status)
    with pytest.raises(ParentTerminalError, match="terminal"):
        plan_pov_schedule(parent, volume_profile=_volume_profile(10))


def test_non_terminal_parent_is_accepted() -> None:
    parent = _parent(status=OrderStatus.SUBMITTED)
    children = plan_pov_schedule(parent, volume_profile=_volume_profile(10))
    assert len(children) == 10


# -- (c)-3: this module only plans POV ---------------------------------------


def test_non_pov_kind_is_rejected() -> None:
    parent = _parent(algo=_algo(kind=AlgoKind.TWAP))
    with pytest.raises(AlgoConstraintError, match="POV"):
        plan_pov_schedule(parent, volume_profile=_volume_profile(10))


# -- (d) determinism (EM-A3) ---------------------------------------------------


def test_same_inputs_produce_an_identical_plan() -> None:
    parent = _parent()
    profile = _volume_profile(10)
    first = plan_pov_schedule(parent, volume_profile=profile)
    second = plan_pov_schedule(parent, volume_profile=profile)
    assert first == second


def test_different_parent_id_changes_child_id_but_not_quantities_or_times() -> None:
    profile = _volume_profile(10)
    parent_a = _parent(parent_id=uuid4())
    parent_b = _parent(parent_id=uuid4(), algo=parent_a.algo, qty=parent_a.qty)
    children_a = plan_pov_schedule(parent_a, volume_profile=profile)
    children_b = plan_pov_schedule(parent_b, volume_profile=profile)
    assert [c.child_id for c in children_a] != [c.child_id for c in children_b]
    assert [c.planned_qty for c in children_a] == [c.planned_qty for c in children_b]
    assert [c.scheduled_at for c in children_a] == [c.scheduled_at for c in children_b]


# -- slice pacing (EM-A3) ------------------------------------------------------


def test_slice_gaps_never_shorter_than_slice_interval_sec() -> None:
    parent = _parent(algo=_algo(slice_interval_sec=60))
    children = plan_pov_schedule(parent, volume_profile=_volume_profile(10))
    for prev, nxt in zip(children, children[1:]):  # noqa: B905 -- pairwise, lengths differ by design
        gap = (nxt.scheduled_at - prev.scheduled_at).total_seconds()
        assert gap >= 60


def test_all_scheduled_times_within_window() -> None:
    parent = _parent()
    children = plan_pov_schedule(parent, volume_profile=_volume_profile(10))
    for child in children:
        assert parent.algo.start <= child.scheduled_at <= parent.algo.end


# -- zero-volume / zero-quantity slices are filtered, never sent to ChildOrder -


def test_zero_volume_slice_is_filtered_out_without_raising() -> None:
    profile = _volume_profile(10)
    profile[3] = Decimal("0")
    parent = _parent()
    children = plan_pov_schedule(parent, volume_profile=profile)
    assert all(c.planned_qty > 0 for c in children)
    assert 3 not in {c.slice_seq for c in children}
    assert len(children) == 9


def test_exhausted_remaining_qty_filters_trailing_slices() -> None:
    """Once remaining qty hits zero, later slices plan zero and are
    dropped -- POV does not force-fill the tail like EM-8's twap.py."""
    parent = _parent(qty=Decimal("100"), algo=_algo(max_participation_pct=Decimal("10")))
    children = plan_pov_schedule(parent, volume_profile=_volume_profile(10, Decimal("1000")))
    assert len(children) == 1
    assert children[0].planned_qty == Decimal("100")
    assert sum(c.planned_qty for c in children) == Decimal("100")


# -- EM-A1 residual guard is actually wired (I-10) ---------------------------


def _imported_top_level_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            found.add(node.module)
    return found


def test_pov_module_imports_all_three_guard_functions() -> None:
    imported = _imported_top_level_modules(_POV_PATH)
    assert any("domain.algo.guard" in module for module in imported), (
        "pov.py must delegate participation/interval/residual checks to "
        "EM-7's guard.py -- see plan_pov_schedule."
    )


def test_pov_module_calls_all_three_guard_functions() -> None:
    source = _POV_PATH.read_text(encoding="utf-8")
    for guard_call in ("check_participation(", "check_slice_interval(", "plan_residual("):
        assert guard_call in source, f"pov.py must call guard.{guard_call} -- wiring regressed."


def test_pov_module_does_not_recompute_the_participation_check_itself() -> None:
    """(a): a bare `/ market_volume` division-comparison would mean pov.py
    reimplements guard.check_participation's own formula instead of
    delegating to it."""
    source = _POV_PATH.read_text(encoding="utf-8")
    assert "/ market_volume" not in source


# -- file size discipline -----------------------------------------------------


def test_pov_module_is_at_most_260_lines() -> None:
    line_count = len(_POV_PATH.read_text(encoding="utf-8").splitlines())
    assert line_count <= 260, f"pov.py has {line_count} lines, exceeding the 260-line leaf cap."
