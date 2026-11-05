"""EM-11 -- `domain/algo/is_shortfall.py` front-load / monotonicity tests.

Split from ``test_algo_is_shortfall.py`` (task-22223) to reduce
pytest-xdist parallel contention on the original file. These tests
exercise urgency-driven front-loading and quantity monotonicity —
independent of DB fixtures and the ``OrdersResidueSentinel`` hook.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

from src.data.models.trading import OrderSide, OrderStatus
from src.foundation.ems.contracts.v1 import (
    AlgoKind,
    AlgoSpec,
    ParentOrder,
    ParentOrderConstraints,
)
from src.foundation.ems.domain.algo.is_shortfall import plan_is_schedule

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
