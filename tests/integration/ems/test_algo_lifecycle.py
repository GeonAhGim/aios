"""EM-15 integration -- `application/{tick_algo,cancel_algo}.py` + `AlgoScheduler`
against the real `orders` table (TEST_DATABASE_URL).

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
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import asyncpg
import pytest

from src.data.models.trading import OrderSide, OrderStatus, OrderType
from src.foundation.ems.application.cancel_algo import cancel_algo
from src.foundation.ems.application.start_algo import AlgoRunPlan
from src.foundation.ems.application.tick_algo import AlgoScheduler, tick_algo
from src.foundation.ems.contracts.v1 import (
    AlgoKind,
    AlgoSpec,
    ChildOrder,
    ParentOrder,
    ParentOrderConstraints,
)
from src.services.oms.adapters.order_repository import PostgresOrderRepository
from src.services.oms.application.submit_order import OrderSubmitDeniedError
from src.services.oms.contracts.v1_commands import CancelOrderCommand
from src.services.oms.contracts.v1_views import OrderView
from tests.integration.oms.conftest import create_test_user

_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _dsn() -> str:
    return os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_dsn(), min_size=1, max_size=8)
    yield p
    await p.close()


async def _insert_order(
    pool: asyncpg.Pool,
    user_id: uuid.UUID,
    *,
    order_id: uuid.UUID | None = None,
    status: str = "ACKNOWLEDGED",
    quantity: Decimal = Decimal("10"),
    committed_child_qty: Decimal = Decimal("0"),
    parent_order_id: uuid.UUID | None = None,
) -> uuid.UUID:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO orders (
                order_id, user_id, client_order_id, strategy_id, strategy_version, symbol,
                exchange, side, order_type, quantity, status, filled_quantity,
                committed_child_qty, parent_order_id
            ) VALUES (COALESCE($1, gen_random_uuid()), $2, $3, 'ems-em15-test', '1.0.0',
                      'BTC/USDT', 'bitget', 'BUY', 'LIMIT', $4, $5, 0, $6, $7)
            RETURNING order_id
            """,
            order_id,
            user_id,
            f"ems-em15-{uuid.uuid4().hex}",
            quantity,
            status,
            committed_child_qty,
            parent_order_id,
        )
    return row["order_id"]


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
    return AlgoSpec(**defaults)


def _parent_order(parent_id: uuid.UUID, *, qty: Decimal) -> ParentOrder:
    return ParentOrder(
        parent_id=parent_id,
        instrument_id="BTC/USDT",
        side=OrderSide.BUY,
        qty=qty,
        algo=_algo(),
        constraints=ParentOrderConstraints(max_participation_pct=Decimal("10")),
        fund_id=uuid.uuid4(),
        portfolio_id=uuid.uuid4(),
        arrival_ts=_T0,
        status=OrderStatus.ACKNOWLEDGED,
    )


def _child(
    parent_id: uuid.UUID, slice_seq: int, qty: Decimal, *, scheduled_at: datetime
) -> ChildOrder:
    return ChildOrder(
        child_id=uuid.uuid4(),
        parent_id=parent_id,
        slice_seq=slice_seq,
        instrument_id="BTC/USDT",
        side=OrderSide.BUY,
        planned_qty=qty,
        scheduled_at=scheduled_at,
    )


def _order_view(order_id: uuid.UUID) -> OrderView:
    now = datetime.now(timezone.utc)
    return OrderView(
        order_id=order_id,
        tenant_id=uuid.uuid4(),
        execution_id=None,
        client_order_id=f"fake-{order_id.hex}",
        exchange_order_id=None,
        symbol="BTC/USDT",
        venue_symbol=None,
        exchange="bitget",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        time_in_force="GTC",
        quantity=Decimal("1"),
        price=None,
        status=OrderStatus.VALIDATED,
        filled_quantity=Decimal("0"),
        average_fill_price=None,
        fee_total=None,
        fee_currency=None,
        version=0,
        parent_order_id=None,
        algo_run_id=None,
        unknown_since=None,
        provider_order_date=None,
        created_at=now,
        updated_at=now,
    )


@dataclass
class _RecordingSubmitter:
    """Fake `submit_child` -- returns a canned `OrderView` per call unless
    `fail_seqs` says this `slice_seq` should raise instead."""

    fail_with: dict[int, Exception] = field(default_factory=dict)
    calls: list[int] = field(default_factory=list)

    async def __call__(self, child: ChildOrder) -> OrderView:
        self.calls.append(child.slice_seq)
        if child.slice_seq in self.fail_with:
            raise self.fail_with[child.slice_seq]
        return _order_view(uuid.uuid4())


async def _committed_qty(pool: asyncpg.Pool, order_id: uuid.UUID) -> Decimal:
    return await pool.fetchval(
        "SELECT committed_child_qty FROM orders WHERE order_id = $1", order_id
    )


# -- happy path ---------------------------------------------------------------


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
        children=[_child(parent_id, 0, Decimal("10"), scheduled_at=now + timedelta(hours=1))],
    )
    submitter = _RecordingSubmitter()

    result = await tick_algo(
        plan, pool=pool, orders_repo=PostgresOrderRepository(), submit_child=submitter, now=now
    )

    assert result.outcomes == []
    assert result.skipped_not_due == 1
    assert submitter.calls == []
    assert await _committed_qty(pool, parent_id) == Decimal("0")


# -- negative: EM-A1/EM-A4 denials at the reservation step ---------------------


async def test_tick_algo_denies_when_slice_would_overcommit_parent_qty(pool):
    """Negative 1 -- reservation fails closed (`AlgoConstraintError`,
    EM-A1) before `submit_child` is ever called."""
    user_id = await create_test_user(pool)
    parent_id = await _insert_order(
        pool, user_id, quantity=Decimal("5"), committed_child_qty=Decimal("3")
    )
    now = datetime.now(timezone.utc)
    plan = AlgoRunPlan(
        parent=_parent_order(parent_id, qty=Decimal("5")),
        children=[_child(parent_id, 0, Decimal("4"), scheduled_at=now - timedelta(seconds=1))],
    )
    submitter = _RecordingSubmitter()

    result = await tick_algo(
        plan, pool=pool, orders_repo=PostgresOrderRepository(), submit_child=submitter, now=now
    )

    assert len(result.outcomes) == 1
    assert result.outcomes[0].order is None
    assert result.outcomes[0].denied_reason is not None
    assert submitter.calls == []  # never reached submit_order
    assert plan.submitted_slice_seqs == set()
    assert await _committed_qty(pool, parent_id) == Decimal("3")  # unchanged


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


# -- gate-red reproduction ------------------------------------------------------


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


# -- failure injection ----------------------------------------------------------


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


# -- performance assertion --------------------------------------------------------


async def test_tick_algo_processes_many_due_slices_within_latency_budget(pool):
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

    started = time.perf_counter()
    result = await tick_algo(
        plan, pool=pool, orders_repo=PostgresOrderRepository(), submit_child=submitter, now=now
    )
    elapsed = time.perf_counter() - started

    assert len(result.outcomes) == 100
    assert elapsed < 15.0, f"tick_algo took {elapsed:.2f}s for 100 slices -- looks superlinear"


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
    assert scheduler.active_plan(parent_id) is None  # auto-unregistered, plan complete


async def test_algo_scheduler_survives_one_run_raising_and_still_ticks_the_rest(pool):
    """Failure injection at the scheduler level -- one registered run whose
    parent row does not exist (simulating a corrupted/raced registration)
    must not stop `tick_once` from advancing every other run."""
    user_id = await create_test_user(pool)
    missing_parent_id = uuid.uuid4()  # no such row -- recompute_parent_aggregate will 404
    healthy_parent_id = await _insert_order(pool, user_id, quantity=Decimal("10"))
    now = datetime.now(timezone.utc)

    broken_plan = AlgoRunPlan(
        parent=_parent_order(missing_parent_id, qty=Decimal("10")), children=[]
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
