"""EM-15b -- `application/get_algo_progress.py` unit tests.

Spec: docs/specs/L4_ems_routing_algos_and_tca_v1.0.md #9 EM-15 residual
scope (progress query, task-4033). Pure function, no DB. ADR-2026-09-09-C
D2 floor: >=3 negative tests, 1 failure-injection, 1 performance assertion,
1 gate-red reproduction -- all covered below (see section markers).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

from src.data.models.trading import OrderSide, OrderStatus
from src.foundation.ems.application.get_algo_progress import (
    AlgoRunNotFoundError,
    get_algo_progress,
)
from src.foundation.ems.application.start_algo import AlgoRunPlan
from src.foundation.ems.contracts.v1 import (
    AlgoKind,
    AlgoSpec,
    ChildOrder,
    ParentOrder,
    ParentOrderConstraints,
)
from tests.support.perf_budget import PerfBudget

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


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


def _parent(parent_id: object, **overrides: object) -> ParentOrder:
    defaults: dict[str, Any] = {
        "parent_id": parent_id,
        "instrument_id": "BTC/USDT",
        "side": OrderSide.BUY,
        "qty": Decimal("10"),
        "algo": _algo(),
        "constraints": ParentOrderConstraints(max_participation_pct=Decimal("10")),
        "fund_id": uuid4(),
        "portfolio_id": uuid4(),
        "arrival_ts": _T0,
        "status": OrderStatus.ACKNOWLEDGED,
    }
    defaults.update(overrides)
    return ParentOrder(**defaults)


def _child(parent_id: object, slice_seq: int, qty: Decimal) -> ChildOrder:
    return ChildOrder(
        child_id=uuid4(),
        parent_id=parent_id,
        slice_seq=slice_seq,
        instrument_id="BTC/USDT",
        side=OrderSide.BUY,
        planned_qty=qty,
        scheduled_at=_T0,
    )


# -- happy path ---------------------------------------------------------------


def test_get_algo_progress_reports_running_with_mixed_submitted_and_pending() -> None:
    parent_id = uuid4()
    plan = AlgoRunPlan(
        parent=_parent(parent_id, qty=Decimal("10")),
        children=[
            _child(parent_id, 0, Decimal("4")),
            _child(parent_id, 1, Decimal("6")),
        ],
        submitted_slice_seqs={0},
    )

    progress = get_algo_progress(plan, parent_id=parent_id)

    assert progress.status == "running"
    assert progress.total_slices == 2
    assert progress.submitted_slices == 1
    assert progress.pending_slices == 1
    assert progress.remaining_qty == Decimal("6")
    assert isinstance(progress.remaining_qty, Decimal)


def test_get_algo_progress_reports_complete_when_no_children_pending() -> None:
    parent_id = uuid4()
    plan = AlgoRunPlan(
        parent=_parent(parent_id, qty=Decimal("10")),
        children=[_child(parent_id, 0, Decimal("10"))],
        submitted_slice_seqs={0},
    )

    progress = get_algo_progress(plan, parent_id=parent_id)

    assert progress.status == "complete"
    assert progress.pending_slices == 0
    assert progress.remaining_qty == Decimal("0")


# -- negative tests -------------------------------------------------------------


def test_get_algo_progress_raises_not_found_for_missing_plan() -> None:
    """Negative 1 -- no plan registered (never started, already unregistered,
    or process restart, see module docstring) is a defined 404-mapped error,
    not a crash or a silently-empty progress."""
    parent_id = uuid4()

    with pytest.raises(AlgoRunNotFoundError):
        get_algo_progress(None, parent_id=parent_id)


def test_get_algo_progress_reports_cancelled_even_with_pending_children() -> None:
    """Negative 2 -- a cancelled run must report `cancelled`, not `running`,
    even though `pending_children()` is non-empty (EM-A4: cancelled means no
    further slices are scheduled, they are not simply "still running")."""
    parent_id = uuid4()
    plan = AlgoRunPlan(
        parent=_parent(parent_id, qty=Decimal("10")),
        children=[_child(parent_id, 0, Decimal("10"))],
        cancelled=True,
    )

    progress = get_algo_progress(plan, parent_id=parent_id)

    assert progress.status == "cancelled"
    assert progress.pending_slices == 1  # still reported, just not "running"


def test_get_algo_progress_reports_demotion_for_a_vwap_run_demoted_to_twap() -> None:
    """Negative 3 -- a VWAP run demoted to TWAP (spec #10 shallow-profile
    demotion, EM-9) must surface that in progress, not silently report a
    plain running VWAP."""
    parent_id = uuid4()
    plan = AlgoRunPlan(
        parent=_parent(parent_id, qty=Decimal("10"), algo=_algo(kind=AlgoKind.VWAP)),
        children=[_child(parent_id, 0, Decimal("10"))],
        demoted_to_twap=True,
        demotion_reason="shallow volume profile",
    )

    progress = get_algo_progress(plan, parent_id=parent_id)

    assert progress.demoted_to_twap is True
    assert progress.demotion_reason == "shallow volume profile"


# -- failure injection ----------------------------------------------------------


def test_get_algo_progress_tolerates_a_stale_submitted_slice_seq() -> None:
    """Failure injection -- a corrupted/raced registration where
    `submitted_slice_seqs` contains a seq that matches no known child (e.g.
    a slice from a stale plan object) must not crash progress computation;
    it is simply excluded from both submitted/pending counts, same as
    `pending_children()`'s own filtering already does."""
    parent_id = uuid4()
    plan = AlgoRunPlan(
        parent=_parent(parent_id, qty=Decimal("10")),
        children=[_child(parent_id, 0, Decimal("10"))],
        submitted_slice_seqs={0, 99},  # 99 does not exist
    )

    progress = get_algo_progress(plan, parent_id=parent_id)

    assert progress.status == "complete"
    assert progress.total_slices == 1
    assert progress.submitted_slices == 1
    assert progress.remaining_qty == Decimal("0")


# -- gate-red reproduction --------------------------------------------------------


def test_get_algo_progress_remaining_qty_is_exact_decimal_not_float_imprecise() -> None:
    """Gate-red reproduction -- CLAUDE.md 'Monetary amounts are Decimal,
    never float'. Reproduces the failure a `float(...)` sum would show
    (0.1 + 0.2 != 0.3 in binary float) and asserts `get_algo_progress`
    stays exact because `remaining_qty` is summed as `Decimal` throughout."""
    parent_id = uuid4()
    plan = AlgoRunPlan(
        parent=_parent(parent_id, qty=Decimal("0.3")),
        children=[
            _child(parent_id, 0, Decimal("0.1")),
            _child(parent_id, 1, Decimal("0.2")),
        ],
    )

    progress = get_algo_progress(plan, parent_id=parent_id)

    assert progress.remaining_qty == Decimal("0.3")
    assert float(Decimal("0.1")) + float(Decimal("0.2")) != 0.3  # the bug this guards against


# -- performance assertion --------------------------------------------------------


@pytest.mark.perf
def test_get_algo_progress_stays_fast_over_max_slice_count(
    perf_budget,
) -> None:
    """Performance assertion -- worst-case slice count (500, EM-8~11's own
    `_MAX_SLICE_COUNT`) must resolve in well under human-perceptible latency;
    threshold generous to avoid CI flakiness while still catching an O(n^2)
    regression in the pure computation.

    Uses `perf_budget.sample(batch=N)` so the total time spans multiple
    ``time.process_time()`` ticks (15.6 ms on Windows) and the per-call
    estimate is not quantised to 0/15.6 ms.  Budget is 50 ms (500 slices
    must stay fast).
    """
    parent_id = uuid4()
    children = [_child(parent_id, i, Decimal("1")) for i in range(500)]
    plan = AlgoRunPlan(
        parent=_parent(parent_id, qty=Decimal("500")),
        children=children,
        submitted_slice_seqs=set(range(0, 500, 2)),
    )

    sample = perf_budget.sample(
        lambda: get_algo_progress(plan, parent_id=parent_id),
        batch=200,
    )
    progress = sample.result

    assert progress.total_slices == 500
    assert progress.submitted_slices == 250
    assert sample.cpu_ms < 50, (
        f"get_algo_progress took {sample.cpu_ms:.3f}ms per call (batch=200) — budget 50ms"
    )


# -- gate-red: delay injection --------------------------------------------------


@pytest.mark.perf
def test_get_algo_progress_perf_assertion_catches_cpu_delay_injection(
    perf_budget: PerfBudget,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gate-red reproduction (D2, task-11539 후속): 성능 예산 단언이 실제로
    위반을 포착하는지 역으로 증명한다. `time.process_time`을 monkeypatch해
    1회 호출당 예산(50ms)을 초과하는 CPU 시간을 가장하면 같은 단언이
    AssertionError로 적색이 돼야 한다.

    단언/마커 삭제나 예산 완화가 아니라, 단언이 "제대로 작동하는지"를
    검증한다. CPU 예산이므로 sleep이 아닌 process_time 교체를 사용한다.
    """
    import time as _time

    _fake_time = [0.0]

    def _fake_process_time() -> float:
        _fake_time[0] += 60.0  # 60ms per call >> 50ms budget
        return _fake_time[0]

    monkeypatch.setattr(_time, "process_time", _fake_process_time)

    parent_id = uuid4()
    children = [_child(parent_id, i, Decimal("1")) for i in range(500)]
    plan = AlgoRunPlan(
        parent=_parent(parent_id, qty=Decimal("500")),
        children=children,
        submitted_slice_seqs=set(range(0, 500, 2)),
    )

    # 같은 측정/단언을 재사용 — budget을 초과하므로 AssertionError가 있어야 함
    with pytest.raises(AssertionError):
        sample = perf_budget.sample(
            lambda: get_algo_progress(plan, parent_id=parent_id),
            batch=1,
        )
        assert sample.cpu_ms < 50, (
            f"get_algo_progress took {sample.cpu_ms:.3f}ms per call — budget 50ms"
        )
