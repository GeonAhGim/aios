"""EM-15 integration -- `cancel_algo` + `AlgoScheduler` against the real
`orders` table (TEST_DATABASE_URL).

Spec: docs/specs/L4_ems_routing_algos_and_tca_v1.0.md #9 EM-15 DoD
("4 algo kinds executed, cancel propagation").

Imports shared helpers/fixtures from conftest.py to avoid duplication.
Split from the 546-line parent (test_algo_lifecycle.py) to respect the
500-line LOC warning (ADR-2026-09-10-C).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.foundation.ems.application.cancel_algo import cancel_algo
from src.foundation.ems.application.start_algo import AlgoRunPlan
from src.foundation.ems.application.tick_algo import AlgoScheduler
from src.services.oms.adapters.order_repository import PostgresOrderRepository
from src.services.oms.contracts.v1_commands import CancelOrderCommand
from src.services.oms.contracts.v1_views import OrderView
from tests.integration.ems.conftest import (
    _child,
    _insert_order,
    _order_view,
    _parent_order,
    _RecordingSubmitter,
)
from tests.integration.oms.conftest import create_test_user

# -- cancel_algo ------------------------------------------------------------------


async def test_cancel_algo_propagates_only_to_open_children(pool):
    user_id = await create_test_user(pool)
    parent_id = await _insert_order(pool, user_id, quantity=Decimal("10"))
    open_child = await _insert_order(
        pool, user_id, quantity=Decimal("3"), parent_order_id=parent_id
    )
    await _insert_order(
        pool, user_id, quantity=Decimal("7"), status="FILLED", parent_order_id=parent_id
    )
    cancelled: list[uuid.UUID] = []

    async def _cancel_child(cmd: CancelOrderCommand) -> OrderView:
        cancelled.append(cmd.order_id)
        return _order_view(cmd.order_id)

    results = await cancel_algo(
        pool=pool,
        orders_repo=PostgresOrderRepository(),
        parent_order_id=parent_id,
        tenant_id=uuid.uuid4(),
        reason="algo cancelled by user",
        actor_subject_id="system",
        cancel_child=_cancel_child,
    )

    assert cancelled == [open_child]
    assert len(results) == 1


async def test_cancel_algo_with_no_open_children_calls_nothing(pool):
    """Negative -- nothing to propagate to is not an error; `cancel_child`
    must never be invoked."""
    user_id = await create_test_user(pool)
    parent_id = await _insert_order(pool, user_id, quantity=Decimal("10"))
    calls: list[uuid.UUID] = []

    async def _cancel_child(cmd: CancelOrderCommand) -> OrderView:
        calls.append(cmd.order_id)
        return _order_view(cmd.order_id)

    results = await cancel_algo(
        pool=pool,
        orders_repo=PostgresOrderRepository(),
        parent_order_id=parent_id,
        tenant_id=uuid.uuid4(),
        reason="none open",
        actor_subject_id="system",
        cancel_child=_cancel_child,
    )

    assert results == []
    assert calls == []


async def test_cancel_algo_propagates_a_child_cancel_failure_and_stops(pool):
    """Negative -- a failed child cancel must not be swallowed as partial
    success; it propagates so the caller knows cancellation did not fully
    land."""
    user_id = await create_test_user(pool)
    parent_id = await _insert_order(pool, user_id, quantity=Decimal("10"))
    await _insert_order(pool, user_id, quantity=Decimal("5"), parent_order_id=parent_id)
    await _insert_order(pool, user_id, quantity=Decimal("5"), parent_order_id=parent_id)

    async def _cancel_child(cmd: CancelOrderCommand) -> OrderView:
        raise RuntimeError("simulated cancel-path outage")

    with pytest.raises(RuntimeError):
        await cancel_algo(
            pool=pool,
            orders_repo=PostgresOrderRepository(),
            parent_order_id=parent_id,
            tenant_id=uuid.uuid4(),
            reason="outage",
            actor_subject_id="system",
            cancel_child=_cancel_child,
        )


# -- AlgoScheduler ------------------------------------------------------------------


async def test_algo_scheduler_ticks_and_unregisters_a_completed_run(pool):
    user_id = await create_test_user(pool)
    parent_id = await _insert_order(pool, user_id, quantity=Decimal("10"))
    now = datetime.now(timezone.utc)
    plan = AlgoRunPlan(
        parent=_parent_order(parent_id, qty=Decimal("10")),
        children=[_child(parent_id, 0, Decimal("10"), scheduled_at=now - timedelta(seconds=1))],
    )
    scheduler = AlgoScheduler(pool, PostgresOrderRepository(), clock=lambda: now)
    scheduler.register(plan, _RecordingSubmitter())

    results = await scheduler.tick_once()

    assert parent_id in results
    assert scheduler.active_plan(parent_id) is None  # completed, unregistered


async def test_algo_scheduler_ignores_slices_already_submitted(pool):
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
    # Mark slice 0 as already submitted.
    plan.submitted_slice_seqs.add(0)

    scheduler = AlgoScheduler(pool, PostgresOrderRepository(), clock=lambda: now)
    submitter = _RecordingSubmitter()
    scheduler.register(plan, submitter)

    results = await scheduler.tick_once()

    assert parent_id in results
    assert submitter.calls == [1]  # only slice 1 was submitted
    assert results


async def test_algo_scheduler_handles_missing_parent_gracefully(pool):
    """Negative -- a plan whose parent row is missing (deleted/consumed)
    must not crash the whole scheduler cycle."""
    user_id = await create_test_user(pool)
    missing_parent_id = uuid.uuid4()
    healthy_parent_id = await _insert_order(pool, user_id, quantity=Decimal("10"))
    now = datetime.now(timezone.utc)

    broken_plan = AlgoRunPlan(
        parent=_parent_order(missing_parent_id, qty=Decimal("10")),
        children=[
            _child(missing_parent_id, 0, Decimal("10"), scheduled_at=now - timedelta(seconds=1))
        ],
    )
    healthy_plan = AlgoRunPlan(
        parent=_parent_order(healthy_parent_id, qty=Decimal("10")),
        children=[
            _child(healthy_parent_id, 0, Decimal("10"), scheduled_at=now - timedelta(seconds=1))
        ],
    )
    scheduler = AlgoScheduler(pool, PostgresOrderRepository(), clock=lambda: now)
    scheduler.register(broken_plan, _RecordingSubmitter())
    scheduler.register(healthy_plan, _RecordingSubmitter())

    results = await scheduler.tick_once()

    assert healthy_parent_id in results
    assert missing_parent_id not in results  # its tick raised and was caught
    assert scheduler.active_plan(missing_parent_id) is not None  # left registered for retry
    assert scheduler.active_plan(healthy_parent_id) is None  # completed, unregistered


async def test_algo_scheduler_run_forever_keeps_looping_across_a_failed_cycle(pool):
    """`run_forever` must not die on a cycle that raises (same convention
    as `ReconcileScheduler`/`LedgerIntegrityScheduler`)."""
    call_count = 0

    async def _boom_sleep(_seconds: float) -> None:
        nonlocal call_count
        call_count += 1
        if call_count >= 2:
            raise asyncio.CancelledError

    scheduler = AlgoScheduler(pool, PostgresOrderRepository(), sleep=_boom_sleep)
    scheduler.register(
        AlgoRunPlan(parent=_parent_order(uuid.uuid4(), qty=Decimal("1")), children=[]),
        _RecordingSubmitter(),
    )  # this run's parent row doesn't exist -- every tick_once raises internally

    with pytest.raises(asyncio.CancelledError):
        await scheduler.run_forever()

    assert call_count == 2  # looped past the first (failed) cycle before we cancelled it
