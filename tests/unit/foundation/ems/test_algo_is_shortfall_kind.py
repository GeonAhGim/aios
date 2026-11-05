"""EM-11 -- `domain/algo/is_shortfall.py` kind guard test.

Split from ``test_algo_is_shortfall.py`` (task-22258) to reduce
pytest-xdist parallel contention on the original file. This test is
independent: it only imports ``AlgoKind`` and ``plan_is_schedule`` —
no shared fixtures, no global state.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
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
    AlgoConstraintError,
    plan_is_schedule,
)

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


# -- (f)-1 kind guard: this module only plans IS -----------------------------


def test_non_is_kind_is_rejected() -> None:
    parent = _parent(algo=_algo(kind=AlgoKind.TWAP))
    with pytest.raises(AlgoConstraintError, match="IS"):
        plan_is_schedule(parent, volume_profile=_abundant_volume_profile(4))
