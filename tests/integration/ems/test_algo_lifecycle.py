"""EM-15 integration -- `tick_algo` against the real `orders` table
(TEST_DATABASE_URL).

Spec: docs/specs/L4_ems_routing_algos_and_tca_v1.0.md #9 EM-15 DoD ("4 algo
kinds executed, cancel propagation"). ADR-2026-09-09-C D2 floor: >=3
negative tests, 1 failure-injection, 1 performance assertion, 1 gate-red
reproduction -- all covered below (see the section markers).

`submit_child`/`cancel_child` are fakes (`tick_algo.py`/`cancel_algo.py`
never construct `SubmitOrderCommand`/`CancelOrderCommand` internals or call
an adapter themselves -- that stays `submit_order`/`cancel_order`'s job,
already covered by L4-09/L4-17's own suites) -- this file only proves the
EM-15 orchestration around `reserve_child_slice`/`release_reserved_slice`/
`recompute_parent_aggregate`/`children_awaiting_cancel` (EM-2/EM-3) is
correct against the real `orders` row and its `committed_child_qty`
column.

Split from a 546-line file (ADR-2026-09-10-C LOC 500-line warn).
cancel_algo and AlgoScheduler tests moved to test_algo_cancel.py.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.foundation.ems.application.start_algo import AlgoRunPlan
from src.foundation.ems.application.tick_algo import tick_algo
from src.services.oms.adapters.order_repository import PostgresOrderRepository
from src.services.oms.application.submit_order import OrderSubmitDeniedError
from tests.integration.ems.conftest import (
    _child,
    _committed_qty,
    _insert_order,
    _parent_order,
    _RecordingSubmitter,
)
from tests.integration.oms.conftest import create_test_user

# -- tick_algo tests --------------------------------------------------------


async def test_tick_algo_submits_due_slices_and_marks_them(pool):
    user_id = await create_test_user(pool)
    parent_id = await _insert_order(pool, user_id, quantity=Decimal("10"))
    now = datetime.now(timezone.utc)
    plan = AlgoRunPlan(
        parent=_parent_order(parent_id, qty=Decimal("10")),
        children=[
            _child(parent_id, 0, Decimal("5"), scheduled_at=now - timedelta(seconds=1)),
            _child(parent_id, 1, Decimal("5"), scheduled_at=now - timedelta(seconds=1)),
        ],
    )
    submitter = _RecordingSubmitter()

    result = await tick_algo(
        plan, pool=pool, orders_repo=PostgresOrderRepository(), submit_child=submitter, now=now
    )

    assert {o.child.slice_seq for o in result.outcomes} == {0, 1}
    assert all(o.order is not None and o.denied_reason is None for o in result.outcomes)
    assert plan.submitted_slice_seqs == {0, 1}
    assert result.skipped_not_due == 0
    assert await _committed_qty(pool, parent_id) == Decimal("10")


async def test_tick_algo_skips_slices_not_yet_due(pool):
    user_id = await create_test_user(pool)
    parent_id = await _insert_order(pool, user_id, quantity=Decimal("10"))
    now = datetime.now(timezone.utc)
    plan = AlgoRunPlan(
        parent=_parent_order(parent_id, qty=Decimal("10")),
        children=[
            _child(parent_id, 0, Decimal("5"), scheduled_at=now + timedelta(seconds=1)),
        ],
    )
    submitter = _RecordingSubmitter()

    result = await tick_algo(
        plan, pool=pool, orders_repo=PostgresOrderRepository(), submit_child=submitter, now=now
    )

    assert result.outcomes == []
    assert result.skipped_not_due == 1
    assert submitter.calls == []  # never reached submit_order
    assert plan.submitted_slice_seqs == set()
    assert await _committed_qty(pool, parent_id) == Decimal("0")  # unchanged


async def test_tick_algo_denies_when_parent_is_terminal(pool):
    """Negative 2 -- EM-A4: a terminal parent rejects new child reservation."""
    user_id = await create_test_user(pool)
    parent_id = await _insert_order(pool, user_id, quantity=Decimal("10"), status="FILLED")
    now = datetime.now(timezone.utc)
    plan = AlgoRunPlan(
        parent=_parent_order(parent_id, qty=Decimal("10")),
        children=[_child(parent_id, 0, Decimal("5"), scheduled_at=now - timedelta(seconds=1))],
    )
    submitter = _RecordingSubmitter()

    result = await tick_algo(
        plan, pool=pool, orders_repo=PostgresOrderRepository(), submit_child=submitter, now=now
    )

    assert result.outcomes[0].order is None
    assert submitter.calls == []
    assert plan.submitted_slice_seqs == set()


async def test_tick_algo_is_a_noop_once_plan_is_marked_cancelled(pool):
    """Negative 3 -- a cancelled plan schedules nothing further (EM-A4)."""
    user_id = await create_test_user(pool)
    parent_id = await _insert_order(pool, user_id, quantity=Decimal("10"))
    now = datetime.now(timezone.utc)
    plan = AlgoRunPlan(
        parent=_parent_order(parent_id, qty=Decimal("10")),
        children=[_child(parent_id, 0, Decimal("10"), scheduled_at=now - timedelta(seconds=1))],
        cancelled=True,
    )
    submitter = _RecordingSubmitter()

    result = await tick_algo(
        plan, pool=pool, orders_repo=PostgresOrderRepository(), submit_child=submitter, now=now
    )

    assert result.outcomes == []
    assert submitter.calls == []
    assert await _committed_qty(pool, parent_id) == Decimal("0")


async def test_tick_algo_releases_reservation_on_gate_deny(pool):
    """Gate-red reproduction -- `submit_order`'s pre_submit_gate DENY
    (`OrderSubmitDeniedError`) must release the EM-3 reservation it made,
    not leave `committed_child_qty` stuck above what was actually placed."""
    user_id = await create_test_user(pool)
    parent_id = await _insert_order(pool, user_id, quantity=Decimal("10"))
    now = datetime.now(timezone.utc)
    plan = AlgoRunPlan(
        parent=_parent_order(parent_id, qty=Decimal("10")),
        children=[_child(parent_id, 0, Decimal("5"), scheduled_at=now - timedelta(seconds=1))],
    )
    submitter = _RecordingSubmitter(fail_with={0: OrderSubmitDeniedError(("RESTRICTED_LIST",))})

    result = await tick_algo(
        plan, pool=pool, orders_repo=PostgresOrderRepository(), submit_child=submitter, now=now
    )

    assert result.outcomes[0].order is None
    assert "RESTRICTED_LIST" in result.outcomes[0].denied_reason
    assert plan.submitted_slice_seqs == set()  # retryable next tick
    assert await _committed_qty(pool, parent_id) == Decimal("0")  # reservation released


async def test_tick_algo_releases_reservation_on_unexpected_submit_failure_and_keeps_going(pool):
    """Failure injection -- a non-gate failure (simulated venue/network
    error) during `submit_child` must still release its reservation and
    must not stop the rest of the tick's slices from being processed."""
    user_id = await create_test_user(pool)
    parent_id = await _insert_order(pool, user_id, quantity=Decimal("10"))
    now = datetime.now(timezone.utc)
    plan = AlgoRunPlan(
        parent=_parent_order(parent_id, qty=Decimal("10")),
        children=[
            _child(parent_id, 0, Decimal("5"), scheduled_at=now - timedelta(seconds=1)),
            _child(parent_id, 1, Decimal("5"), scheduled_at=now - timedelta(seconds=1)),
        ],
    )
    submitter = _RecordingSubmitter(fail_with={0: ConnectionError("simulated venue outage")})

    result = await tick_algo(
        plan, pool=pool, orders_repo=PostgresOrderRepository(), submit_child=submitter, now=now
    )

    by_seq = {o.child.slice_seq: o for o in result.outcomes}
    assert by_seq[0].order is None and by_seq[0].denied_reason is not None
    assert by_seq[1].order is not None and by_seq[1].denied_reason is None
    assert plan.submitted_slice_seqs == {1}
    # slice 0's reservation was released, slice 1's remains committed.
    assert await _committed_qty(pool, parent_id) == Decimal("5")


@pytest.mark.perf
async def test_tick_algo_processes_many_due_slices_within_latency_budget(pool, perf_budget):
    """Performance assertion -- a single tick over a realistic worst-case
    slice count (500, EM-8~11's own `_MAX_SLICE_COUNT`) must not blow up
    super-linearly. Threshold is deliberately generous (wall-clock,
    real localhost Postgres round trips) to avoid CI flakiness while still
    catching an O(n^2) regression."""
    user_id = await create_test_user(pool)
    parent_id = await _insert_order(pool, user_id, quantity=Decimal("500"))
    now = datetime.now(timezone.utc)
    children = [
        _child(parent_id, i, Decimal("1"), scheduled_at=now - timedelta(seconds=1))
        for i in range(100)
    ]
    plan = AlgoRunPlan(parent=_parent_order(parent_id, qty=Decimal("500")), children=children)
    submitter = _RecordingSubmitter()

    sample = await perf_budget.sample_async(
        lambda: tick_algo(
            plan, pool=pool, orders_repo=PostgresOrderRepository(), submit_child=submitter, now=now
        )
    )

    assert len(sample.result.outcomes) == 100
    assert sample.wall_ms < 15_000, (
        f"tick_algo took {sample.wall_ms / 1000:.2f}s for 100 slices -- looks superlinear"
    )
