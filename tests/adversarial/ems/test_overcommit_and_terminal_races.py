"""EM-16 adversarial suite -- concurrent overcommit and terminal-parent
attacks against `application/{start_algo,tick_algo}.py`'s `AlgoRunPlan`
reservation path (EM-15, task-2670).

Spec: docs/specs/L4_ems_routing_algos_and_tca_v1.0.md #8 ("부모 초과 슬라이스,
참여율 조작, 터미널 부모에 자식 생성"), #9 EM-16 (depends EM-15, DoD "전 케이스
차단"). `tests/adversarial/ems/test_child_order_bypass.py` already proves
EM-A2's one gate-bypass kind (task-2121 audit note: "우회 1종만"); this file
is the "초과·터미널 케이스 추가" the same note calls for, now that EM-15's
`tick_algo`/`AlgoScheduler` exist to attack for real instead of only via
`reserve_child_slice` unit mocks.

Every scenario here drives real concurrent `asyncio` tasks against the real
`orders` row (TEST_DATABASE_URL) -- a sequential negative test proves the
domain function rejects a bad single call; these prove the `FOR UPDATE` row
lock (`reserve_child_slice`/`recompute_parent_aggregate`'s
`get_for_update`/`transition`) actually serializes two colluding or racing
callers so EM-A1 (no overcommit) and EM-A4 (nothing survives a terminal
parent) hold under real contention, not just in a single-threaded call.

ADR-2026-09-09-C D2 floor: negative x4 (below), 1 terminal race, 1 failure
injection, 1 gate-red reproduction, 1 performance assertion -- see section
markers. depth=D2.
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
from src.foundation.ems.application.aggregate_parent import recompute_parent_aggregate
from src.foundation.ems.application.start_algo import AlgoRunPlan
from src.foundation.ems.application.tick_algo import tick_algo
from src.foundation.ems.contracts.v1 import (
    AlgoKind,
    AlgoSpec,
    ChildOrder,
    ParentOrder,
    ParentOrderConstraints,
)
from src.services.oms.adapters.order_repository import PostgresOrderRepository
from src.services.oms.application.submit_order import OrderSubmitDeniedError
from src.services.oms.contracts.v1_views import OrderView
from tests.integration.oms.conftest import create_test_user

_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
_CONCURRENT_RUNS = 15


def _dsn() -> str:
    return os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_dsn(), min_size=4, max_size=_CONCURRENT_RUNS + 10)
    yield p
    await p.close()


async def _insert_order(
    pool: asyncpg.Pool,
    user_id: uuid.UUID,
    *,
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
            ) VALUES (gen_random_uuid(), $1, $2, 'ems-em16-test', '1.0.0', 'BTC/USDT',
                      'bitget', 'BUY', 'LIMIT', $3, $4, 0, $5, $6)
            RETURNING order_id
            """,
            user_id,
            f"ems-em16-{uuid.uuid4().hex}",
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
class _Submitter:
    """Fake `submit_child` -- raises `fail_with[slice_seq]` if set, else succeeds."""

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


async def _status(pool: asyncpg.Pool, order_id: uuid.UUID) -> str:
    return await pool.fetchval("SELECT status FROM orders WHERE order_id = $1", order_id)


def _plan_with_one_slice(parent_id: uuid.UUID, qty: Decimal, slice_seq: int = 0) -> AlgoRunPlan:
    now = datetime.now(timezone.utc)
    return AlgoRunPlan(
        parent=_parent_order(parent_id, qty=Decimal("10")),
        children=[_child(parent_id, slice_seq, qty, scheduled_at=now - timedelta(seconds=1))],
    )


def _tick(
    plan: AlgoRunPlan,
    pool: asyncpg.Pool,
    repo: PostgresOrderRepository,
    submitter: _Submitter,
    now: datetime,
):
    return tick_algo(plan, pool=pool, orders_repo=repo, submit_child=submitter, now=now)


# -- negative: concurrent overcommit attempts ---------------------------------


async def test_concurrent_overcommit_only_lets_capacity_through(pool):
    """Negative 1 -- two colluding/racing algo runs each try to reserve 6
    against a parent with qty=10 (12 requested total). Only one may win;
    `committed_child_qty` must never exceed 10."""
    user_id = await create_test_user(pool)
    parent_id = await _insert_order(pool, user_id, quantity=Decimal("10"))
    repo = PostgresOrderRepository()
    submitter_a, submitter_b = _Submitter(), _Submitter()
    now = datetime.now(timezone.utc)

    result_a, result_b = await asyncio.gather(
        _tick(_plan_with_one_slice(parent_id, Decimal("6")), pool, repo, submitter_a, now),
        _tick(_plan_with_one_slice(parent_id, Decimal("6")), pool, repo, submitter_b, now),
    )

    outcomes = [result_a.outcomes[0], result_b.outcomes[0]]
    succeeded = [o for o in outcomes if o.order is not None]
    denied = [o for o in outcomes if o.order is None]
    assert len(succeeded) == 1
    assert len(denied) == 1
    assert "exceeding parent qty" in denied[0].denied_reason
    assert await _committed_qty(pool, parent_id) == Decimal("6")


async def test_concurrent_overcommit_at_exact_boundary_admits_both_no_lost_update(pool):
    """Negative 2 -- two concurrent slices of 5 each sum exactly to parent
    qty=10 (the accepted boundary, EM-A1). Both must be admitted and the
    final sum must be exactly 10 -- a naive read-then-write race would lose
    one of the two updates."""
    user_id = await create_test_user(pool)
    parent_id = await _insert_order(pool, user_id, quantity=Decimal("10"))
    repo = PostgresOrderRepository()
    now = datetime.now(timezone.utc)

    result_a, result_b = await asyncio.gather(
        _tick(_plan_with_one_slice(parent_id, Decimal("5")), pool, repo, _Submitter(), now),
        _tick(_plan_with_one_slice(parent_id, Decimal("5")), pool, repo, _Submitter(), now),
    )

    assert result_a.outcomes[0].order is not None
    assert result_b.outcomes[0].order is not None
    assert await _committed_qty(pool, parent_id) == Decimal("10")


async def test_single_tick_burst_admits_up_to_capacity_then_denies_rest(pool):
    """Negative 3 -- one tick fed a burst of 3 due slices (4+4+4=12) against
    qty=10 (a single caller trying to smuggle an over-quota schedule past
    `start_algo` straight into `tick_algo`). Sequential admission inside the
    one tick call must stop exactly at capacity."""
    user_id = await create_test_user(pool)
    parent_id = await _insert_order(pool, user_id, quantity=Decimal("10"))
    now = datetime.now(timezone.utc)
    plan = AlgoRunPlan(
        parent=_parent_order(parent_id, qty=Decimal("10")),
        children=[
            _child(parent_id, i, Decimal("4"), scheduled_at=now - timedelta(seconds=1))
            for i in range(3)
        ],
    )
    submitter = _Submitter()

    result = await _tick(plan, pool, PostgresOrderRepository(), submitter, now)

    admitted = [o for o in result.outcomes if o.order is not None]
    denied = [o for o in result.outcomes if o.order is None]
    assert len(admitted) == 2
    assert len(denied) == 1
    assert await _committed_qty(pool, parent_id) == Decimal("8")


async def test_concurrent_reservations_against_already_terminal_parent_all_denied(pool):
    """Negative 4 -- EM-A4: a parent already CANCELLED before any tick
    denies every concurrent reservation attempt, none reach `submit_child`
    (no gate bypass under concurrent load either)."""
    user_id = await create_test_user(pool)
    parent_id = await _insert_order(pool, user_id, quantity=Decimal("10"), status="CANCELLED")
    repo = PostgresOrderRepository()
    submitter_a, submitter_b = _Submitter(), _Submitter()
    now = datetime.now(timezone.utc)

    result_a, result_b = await asyncio.gather(
        _tick(_plan_with_one_slice(parent_id, Decimal("1")), pool, repo, submitter_a, now),
        _tick(_plan_with_one_slice(parent_id, Decimal("1")), pool, repo, submitter_b, now),
    )

    for result in (result_a, result_b):
        assert result.outcomes[0].order is None
        assert "is terminal" in result.outcomes[0].denied_reason
    assert submitter_a.calls == [] and submitter_b.calls == []
    assert await _committed_qty(pool, parent_id) == Decimal("0")


# -- terminal race -------------------------------------------------------------


async def test_concurrent_terminal_transition_races_reservation_then_blocks_all_further_slices(
    pool,
):
    """A parent rollup to CANCELLED (`recompute_parent_aggregate`, EM-2/EM-3
    -- all children terminal with zero fill) races a concurrent reservation
    attempt. Whichever wins the row lock is a legitimate outcome (the
    reservation may have genuinely landed a moment before the cancel), but
    once the race settles the parent must be permanently terminal -- proven
    by a deterministic follow-up reservation attempt that must be denied no
    matter which side of the race it landed on."""
    user_id = await create_test_user(pool)
    parent_id = await _insert_order(pool, user_id, quantity=Decimal("10"))
    await _insert_order(
        pool, user_id, quantity=Decimal("3"), status="CANCELLED", parent_order_id=parent_id
    )
    repo = PostgresOrderRepository()
    now = datetime.now(timezone.utc)

    async def _cancel_rollup() -> None:
        async with pool.acquire() as conn, conn.transaction():
            await recompute_parent_aggregate(
                repo, conn, parent_order_id=parent_id, trace_id=parent_id, occurred_at=now
            )

    reserve_result, _ = await asyncio.gather(
        _tick(_plan_with_one_slice(parent_id, Decimal("5")), pool, repo, _Submitter(), now),
        _cancel_rollup(),
    )

    assert await _status(pool, parent_id) == "CANCELLED"
    assert await _committed_qty(pool, parent_id) in (Decimal("0"), Decimal("5"))
    if reserve_result.outcomes[0].order is None:
        assert "is terminal" in reserve_result.outcomes[0].denied_reason

    followup = await _tick(
        _plan_with_one_slice(parent_id, Decimal("1"), slice_seq=99), pool, repo, _Submitter(), now
    )
    assert followup.outcomes[0].order is None
    assert "is terminal" in followup.outcomes[0].denied_reason


# -- failure injection ----------------------------------------------------------


async def test_concurrent_boundary_reservation_with_injected_submit_failure_fully_releases(pool):
    """Failure injection -- both concurrent slices (5+5, boundary) win their
    reservation, but one's `submit_child` raises a simulated venue outage.
    Its reservation must be released even while the sibling reservation is
    concurrently active on the same parent row -- no permanent lock leak
    under contention."""
    user_id = await create_test_user(pool)
    parent_id = await _insert_order(pool, user_id, quantity=Decimal("10"))
    repo = PostgresOrderRepository()
    now = datetime.now(timezone.utc)
    failing = _Submitter(fail_with={0: ConnectionError("simulated venue outage")})
    healthy = _Submitter()

    result_fail, result_ok = await asyncio.gather(
        _tick(_plan_with_one_slice(parent_id, Decimal("5")), pool, repo, failing, now),
        _tick(_plan_with_one_slice(parent_id, Decimal("5")), pool, repo, healthy, now),
    )

    assert result_fail.outcomes[0].order is None
    assert result_fail.outcomes[0].denied_reason is not None
    assert result_ok.outcomes[0].order is not None
    assert await _committed_qty(pool, parent_id) == Decimal("5")  # only the healthy one remains


# -- gate-red reproduction -------------------------------------------------------


async def test_concurrent_boundary_reservation_with_gate_deny_releases_under_load(pool):
    """Gate-red reproduction under concurrent load -- one of two boundary
    (5+5) reservations is denied by `submit_order`'s CM-8 gate
    (`OrderSubmitDeniedError`) while its sibling is concurrently submitting
    on the same parent. The denied slice's reservation must still be
    released, leaving only the gate-approved slice committed."""
    user_id = await create_test_user(pool)
    parent_id = await _insert_order(pool, user_id, quantity=Decimal("10"))
    repo = PostgresOrderRepository()
    now = datetime.now(timezone.utc)
    denied_submitter = _Submitter(fail_with={0: OrderSubmitDeniedError(("RESTRICTED_LIST",))})
    allowed_submitter = _Submitter()

    result_denied, result_allowed = await asyncio.gather(
        _tick(_plan_with_one_slice(parent_id, Decimal("5")), pool, repo, denied_submitter, now),
        _tick(_plan_with_one_slice(parent_id, Decimal("5")), pool, repo, allowed_submitter, now),
    )

    assert result_denied.outcomes[0].order is None
    assert "RESTRICTED_LIST" in result_denied.outcomes[0].denied_reason
    assert result_allowed.outcomes[0].order is not None
    assert await _committed_qty(pool, parent_id) == Decimal("5")


# -- performance assertion --------------------------------------------------------


async def test_many_concurrent_algo_runs_stay_within_latency_budget(pool):
    """Performance assertion -- an adversarial flood of `_CONCURRENT_RUNS`
    distinct algo runs ticking simultaneously (each its own parent, no lock
    contention between them) must not blow up super-linearly. Threshold is
    deliberately generous (wall-clock, real localhost Postgres round trips)
    to avoid CI flakiness while still catching an O(n^2) regression."""
    user_id = await create_test_user(pool)
    repo = PostgresOrderRepository()
    now = datetime.now(timezone.utc)
    parent_ids = [
        await _insert_order(pool, user_id, quantity=Decimal("10")) for _ in range(_CONCURRENT_RUNS)
    ]
    plans = [_plan_with_one_slice(pid, Decimal("10")) for pid in parent_ids]

    started = time.perf_counter()
    results = await asyncio.gather(*[_tick(plan, pool, repo, _Submitter(), now) for plan in plans])
    elapsed = time.perf_counter() - started

    assert all(r.outcomes[0].order is not None for r in results)
    assert elapsed < 20.0, (
        f"{_CONCURRENT_RUNS} concurrent algo ticks took {elapsed:.2f}s -- looks superlinear"
    )
