"""EM-15a -- `application/start_algo.py` unit tests.

Spec: docs/specs/L4_ems_routing_algos_and_tca_v1.0.md #9 EM-15 DoD
("4 algo kinds executed"). Pure dispatch -- no DB.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

from src.data.models.trading import OrderSide, OrderStatus
from src.foundation.ems.application.start_algo import (
    AlgoRunPlan,
    UnknownAlgoKindError,
    start_algo,
)
from src.foundation.ems.contracts.v1 import (
    AlgoKind,
    AlgoSpec,
    ParentOrder,
    ParentOrderConstraints,
)
from src.foundation.ems.domain.algo.twap import ParentTerminalError

_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _algo(**overrides: object) -> AlgoSpec:
    defaults: dict[str, Any] = {
        "kind": AlgoKind.TWAP,
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
        "qty": Decimal("10"),
        "algo": _algo(),
        "constraints": ParentOrderConstraints(max_participation_pct=Decimal("10")),
        "fund_id": uuid4(),
        "portfolio_id": uuid4(),
        "arrival_ts": _T0,
        "status": OrderStatus.CREATED,
    }
    defaults.update(overrides)
    return ParentOrder(**defaults)


def _profile(slice_count: int, *, volume: Decimal = Decimal("1000000")) -> list[Decimal]:
    return [volume] * slice_count


@pytest.mark.parametrize("kind", [AlgoKind.TWAP, AlgoKind.POV, AlgoKind.IS])
def test_start_algo_dispatches_each_of_the_three_flat_kinds(kind: AlgoKind) -> None:
    parent = _parent(algo=_algo(kind=kind))
    plan = start_algo(parent, volume_profile=_profile(10))

    assert isinstance(plan, AlgoRunPlan)
    assert plan.parent is parent
    assert plan.children  # a non-empty deterministic schedule was produced
    assert sum((c.planned_qty for c in plan.children), Decimal("0")) == parent.qty
    assert plan.demoted_to_twap is False
    assert plan.demotion_reason is None


def test_start_algo_dispatches_vwap_and_carries_demotion_fields() -> None:
    parent = _parent(algo=_algo(kind=AlgoKind.VWAP))
    # A single nonzero slice out of 10 is well below EM-9's 50% coverage floor
    # -> VWAP demotes to equal-weight TWAP allocation (spec #10).
    thin_profile = [Decimal("1000000")] + [Decimal("0")] * 9

    plan = start_algo(parent, volume_profile=thin_profile)

    assert plan.demoted_to_twap is True
    assert plan.demotion_reason is not None
    assert sum((c.planned_qty for c in plan.children), Decimal("0")) == parent.qty


def test_start_algo_rejects_iceberg_with_no_wired_planner() -> None:
    """Negative -- `AlgoKind.ICEBERG` is a valid EM-1 contract value but has
    no EM-8~11 execution planner (spec #1 only requires 4 kinds); dispatch
    must fail closed instead of silently mapping it onto another kind."""
    parent = _parent(algo=_algo(kind=AlgoKind.ICEBERG))

    with pytest.raises(UnknownAlgoKindError):
        start_algo(parent, volume_profile=_profile(10))


def test_start_algo_propagates_parent_terminal_from_the_chosen_planner() -> None:
    """Negative -- EM-A4: a terminal parent may not start scheduling new
    slices. `start_algo` does not re-check this itself; it must not swallow
    the error the underlying EM-8 planner (twap.py) already raises."""
    parent = _parent(algo=_algo(kind=AlgoKind.TWAP), status=OrderStatus.FILLED)

    with pytest.raises(ParentTerminalError):
        start_algo(parent, volume_profile=_profile(10))


def test_start_algo_rejects_volume_profile_length_mismatch() -> None:
    """Negative -- a `volume_profile` shorter than the derived slice count is
    never treated as "unlimited volume" for the missing tail (twap.py/
    pov.py/is_shortfall.py all fail closed on this; dispatch must not paper
    over it)."""
    parent = _parent(algo=_algo(kind=AlgoKind.POV))

    with pytest.raises(ValueError):
        start_algo(parent, volume_profile=_profile(3))


def test_algo_run_plan_pending_children_shrinks_as_slices_are_marked_submitted() -> None:
    parent = _parent(algo=_algo(kind=AlgoKind.TWAP))
    plan = start_algo(parent, volume_profile=_profile(10))
    assert not plan.is_complete()

    for child in plan.children:
        plan.submitted_slice_seqs.add(child.slice_seq)

    assert plan.pending_children() == []
    assert plan.is_complete()
