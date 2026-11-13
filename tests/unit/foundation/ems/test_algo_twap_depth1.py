"""EM-8 `domain/algo/twap.py` D1->D2 depth top-up (perf assertion, extra negatives).

Supplements test_algo_twap.py (not modified). No DB -- pure function tests.

Perf budget (new, ADR-2026-09-09-C Decision 1 style): planning a 10-slice TWAP schedule
costs ~0.08 ms cpu per call locally (batch=200 -> ~16 ms per batch, process_time). The budget
is _BUDGET_MS cpu_ms per batch of 200 plans, ~32x headroom over that measurement.
"""

from __future__ import annotations

from collections.abc import Callable
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
from src.foundation.ems.domain.algo.guard import ParticipationExceededError
from src.foundation.ems.domain.algo.twap import (
    AlgoConstraintError,
    ParentTerminalError,
    plan_twap_schedule,
)
from tests.conftest import PerfBudget
from tests.support.perf_inject import reset_virtual_totals, virtual_delayed_sync

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_BUDGET_MS = 500.0  # cpu_ms per batch of 200 plan_twap_schedule calls (measured ~16 ms)
_BATCH = 200


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


def _profile(n: int = 10) -> list[Decimal]:
    return [Decimal("1000000")] * n


# -- negatives ----------------------------------------------------------------


def test_depth1_negative_profile_length_mismatch_rejected() -> None:
    with pytest.raises(AlgoConstraintError):
        plan_twap_schedule(_parent(), volume_profile=_profile(9))


def test_depth1_negative_non_twap_kind_rejected() -> None:
    parent = _parent(algo=_algo(kind=AlgoKind.VWAP))
    with pytest.raises(AlgoConstraintError):
        plan_twap_schedule(parent, volume_profile=_profile())


def test_depth1_negative_filled_parent_rejected() -> None:
    parent = _parent(status=OrderStatus.FILLED)
    with pytest.raises(ParentTerminalError):
        plan_twap_schedule(parent, volume_profile=_profile())


def test_depth1_negative_zero_volume_rejected_and_nothing_returned() -> None:
    with pytest.raises(ParticipationExceededError):
        plan_twap_schedule(_parent(), volume_profile=[Decimal("0")] * 10)


# -- perf ---------------------------------------------------------------------


def _measure(perf_budget: PerfBudget, fn: Callable[[], object]) -> float:
    return perf_budget.best_of(fn, n=10, warmup=1, batch=_BATCH).cpu_ms


@pytest.mark.perf
def test_depth1_plan_twap_schedule_throughput_budget(perf_budget: PerfBudget) -> None:
    parent = _parent()
    profile = _profile()
    cpu_ms = _measure(perf_budget, lambda: plan_twap_schedule(parent, volume_profile=profile))
    assert cpu_ms < _BUDGET_MS, f"budget exceeded: {cpu_ms} ms"


@pytest.mark.perf
def test_depth1_plan_twap_schedule_goes_red_with_10x_delay_injection(
    perf_budget: PerfBudget,
) -> None:
    """Gate-red: a virtual 10x-budget delay makes the unchanged budget assertion fail."""
    reset_virtual_totals()
    parent = _parent()
    profile = _profile()

    def _run() -> object:
        return plan_twap_schedule(parent, volume_profile=profile)

    slowed = virtual_delayed_sync(_run, _BUDGET_MS * 10 / 1000.0)
    cpu_ms = _measure(perf_budget, slowed)
    with pytest.raises(AssertionError):
        assert cpu_ms < _BUDGET_MS, f"budget exceeded: {cpu_ms} ms"
