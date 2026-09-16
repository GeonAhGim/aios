"""EM-15b -- application/tick_algo.py: advance one running algo by one tick,
plus the resident scheduler (`AlgoScheduler`) that drives every registered
run on an interval.

Spec: docs/specs/L4_ems_routing_algos_and_tca_v1.0.md #2 module table
(`application/{start_algo,tick_algo,cancel_algo}.py` + scheduler wiring +
integration), #9 EM-15.

`tick_algo` consumes the `AlgoRunPlan` `start_algo.py` produced: for every
slice whose `scheduled_at` has arrived and that has not been submitted yet,
it reserves the slice against the parent (`aggregate_parent.reserve_child_slice`,
EM-3 -- EM-A1/EM-A4 enforced there), hands the slice to the caller-supplied
`submit_child` to actually pass it through `submit_order` (spec #3: "child
orders pass through submit_order with no exception" -- this module never
constructs a `SubmitOrderCommand` or calls an adapter itself, the same
separation `route_order.py`/`aggregate_parent.py` already keep), and rolls
the parent up afterward (`aggregate_parent.recompute_parent_aggregate`,
EM-2/EM-3).

A denied or failed slice (gate DENY, i.e. `OrderSubmitDeniedError`, or any
other exception `submit_child` raises -- a simulated venue/network failure)
releases its reservation (`release_reserved_slice`) and is recorded in the
`TickResult` instead of being marked submitted, so the slice is retried on
the next tick rather than silently dropping quantity (EM-A1) or leaving
`committed_child_qty` permanently stuck above what was actually placed.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

import asyncpg

from src.foundation.ems.application.aggregate_parent import (
    recompute_parent_aggregate,
    release_reserved_slice,
    reserve_child_slice,
)
from src.foundation.ems.application.start_algo import AlgoRunPlan
from src.foundation.ems.contracts.v1 import ChildOrder
from src.services.oms.contracts.v1_views import OrderView
from src.services.oms.ports.repository import OrderRepoPort

logger = logging.getLogger(__name__)

SubmitChild = Callable[[ChildOrder], Awaitable[OrderView]]

DEFAULT_INTERVAL_SEC = 2.0
"""Scheduler cadence -- deployment can tune it; this default is finer than
any `AlgoSpec.slice_interval_sec` a caller is expected to configure, so a
due slice is picked up promptly without polling too aggressively."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class TickOutcome:
    """One slice's fate this tick. `order` is set only on a real
    `submit_order` success; `denied_reason` is set exactly when `order` is
    not (mutually exclusive, matching `AlgoRunPlan`'s "submitted means an
    `OrderView` exists" contract)."""

    child: ChildOrder
    order: OrderView | None
    denied_reason: str | None


@dataclass(frozen=True)
class TickResult:
    outcomes: list[TickOutcome]
    skipped_not_due: int


async def tick_algo(
    plan: AlgoRunPlan,
    *,
    pool: asyncpg.Pool,
    orders_repo: OrderRepoPort,
    submit_child: SubmitChild,
    now: datetime,
) -> TickResult:
    """One scheduler tick for `plan`. A no-op (empty result, no DB write) if
    `plan.cancelled` -- `cancel_algo.py` marks that flag, and a cancelled
    run must not schedule further children (EM-A4)."""
    if plan.cancelled:
        return TickResult(outcomes=[], skipped_not_due=0)

    pending = plan.pending_children()
    due = [c for c in pending if c.scheduled_at <= now]
    outcomes: list[TickOutcome] = []

    for child in due:
        try:
            async with pool.acquire() as conn, conn.transaction():
                await reserve_child_slice(
                    orders_repo,
                    conn,
                    parent_order_id=plan.parent.parent_id,
                    new_slice_qty=child.planned_qty,
                )
        except Exception as exc:  # noqa: BLE001 -- fail-closed: any reservation failure
            # (AlgoConstraintError/ParentTerminalError, EM-A1/EM-A4) denies this slice
            # rather than crashing the whole tick.
            logger.warning(
                "tick_algo: parent=%s slice_seq=%s reservation denied: %s",
                plan.parent.parent_id,
                child.slice_seq,
                exc,
            )
            outcomes.append(TickOutcome(child=child, order=None, denied_reason=str(exc)))
            continue

        try:
            order = await submit_child(child)
        except Exception as exc:  # noqa: BLE001 -- fail-closed: gate DENY
            # (OrderSubmitDeniedError) or any other injected submit failure both release
            # the reservation and deny this slice rather than crashing the whole tick.
            logger.warning(
                "tick_algo: parent=%s slice_seq=%s submit failed, releasing reservation: %s",
                plan.parent.parent_id,
                child.slice_seq,
                exc,
            )
            async with pool.acquire() as conn, conn.transaction():
                await release_reserved_slice(
                    orders_repo,
                    conn,
                    parent_order_id=plan.parent.parent_id,
                    slice_qty=child.planned_qty,
                )
            outcomes.append(TickOutcome(child=child, order=None, denied_reason=str(exc)))
            continue

        plan.submitted_slice_seqs.add(child.slice_seq)
        outcomes.append(TickOutcome(child=child, order=order, denied_reason=None))

    async with pool.acquire() as conn, conn.transaction():
        await recompute_parent_aggregate(
            orders_repo,
            conn,
            parent_order_id=plan.parent.parent_id,
            trace_id=plan.parent.parent_id,
            occurred_at=now,
        )

    return TickResult(outcomes=outcomes, skipped_not_due=len(pending) - len(due))


@dataclass
class _ActiveRun:
    plan: AlgoRunPlan
    submit_child: SubmitChild


class AlgoScheduler:
    """Resident loop -- same "one cycle's failure never kills the loop"
    convention as `ReconcileScheduler`/`LedgerIntegrityScheduler`
    (`src/services/oms/application/reconcile_scheduler.py`,
    `src/foundation/ledger/application/scheduler.py`): no new resident-loop
    framework is invented here.

    Registration is in-memory, per process (see `start_algo.py`'s
    docstring on why no durable algo-run store exists yet) -- whatever
    wires `start_algo` into a request path calls `register()` right after,
    and `cancel_algo.py`'s caller calls `unregister()` (or lets a completed
    run fall out on its own, see `tick_once`).
    """

    def __init__(
        self,
        pool: asyncpg.Pool,
        orders_repo: OrderRepoPort,
        *,
        interval_sec: float = DEFAULT_INTERVAL_SEC,
        clock: Callable[[], datetime] = _utcnow,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._pool = pool
        self._orders_repo = orders_repo
        self._interval_sec = interval_sec
        self._clock = clock
        self._sleep = sleep
        self._active: dict[UUID, _ActiveRun] = {}

    def register(self, plan: AlgoRunPlan, submit_child: SubmitChild) -> None:
        self._active[plan.parent.parent_id] = _ActiveRun(plan=plan, submit_child=submit_child)

    def unregister(self, parent_order_id: UUID) -> None:
        self._active.pop(parent_order_id, None)

    def active_plan(self, parent_order_id: UUID) -> AlgoRunPlan | None:
        run = self._active.get(parent_order_id)
        return run.plan if run is not None else None

    async def tick_once(self) -> dict[UUID, TickResult]:
        """One cycle over every registered run. A run whose tick raises is
        logged and left registered for retry next cycle (same as the other
        two schedulers); a run that finishes (no pending children, or was
        marked cancelled) is dropped so `tick_once` stops visiting it."""
        now = self._clock()
        results: dict[UUID, TickResult] = {}
        for parent_order_id, run in list(self._active.items()):
            try:
                results[parent_order_id] = await tick_algo(
                    run.plan,
                    pool=self._pool,
                    orders_repo=self._orders_repo,
                    submit_child=run.submit_child,
                    now=now,
                )
            except Exception:
                logger.exception(
                    "algo_scheduler: parent=%s tick failed -- retrying next cycle",
                    parent_order_id,
                )
                continue
            if run.plan.cancelled or run.plan.is_complete():
                self.unregister(parent_order_id)
        return results

    async def run_forever(self) -> None:
        """main.py/background_loops.py background task body."""
        while True:
            try:
                await self.tick_once()
            except Exception:
                logger.exception(
                    "algo_scheduler: this cycle failed entirely -- retrying next cycle"
                )
            await self._sleep(self._interval_sec)
