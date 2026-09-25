"""EM-15c -- application/cancel_algo.py: propagate a parent cancel to its
open children.

Spec: docs/specs/L4_ems_routing_algos_and_tca_v1.0.md #2 module table
(`application/{start_algo,tick_algo,cancel_algo}.py` + scheduler wiring +
integration), #4 EM-A4 ("terminal parent: no new child, open children get a
cancel request"), #9 EM-15.

`cancel_algo` reuses EM-3's `children_awaiting_cancel` (already-open, i.e.
non-terminal, child `order_id`s -- `domain/parent_child.py`'s
`children_pending_cancellation`) and issues one `CancelOrderCommand` per
child through the caller-supplied `cancel_child` (bound to
`src.services.oms.application.cancel_order.cancel_order` at the wiring
site) -- same separation `tick_algo.py` keeps for `submit_order`: this
module never calls an adapter or builds an `OrderView` itself.

This function does not flip the parent's own order status -- callers that
also want the parent order itself cancelled call `cancel_order` on it
directly (e.g. right before calling this function) and mark the in-memory
`AlgoRunPlan.cancelled = True` (`tick_algo.py`) so `AlgoScheduler` stops
scheduling new slices against it. A child cancel failure is not swallowed:
it propagates immediately, leaving any later children in `open_child_ids`
unprocessed, because a request to cancel is not the same as confirmation
that it happened, and hiding a failed cancel behind partial success would
misreport how much of the parent is actually still live.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID, uuid4

import asyncpg

from src.foundation.ems.application.aggregate_parent import children_awaiting_cancel
from src.services.oms.contracts.v1_commands import CancelOrderCommand
from src.services.oms.contracts.v1_views import OrderView
from src.services.oms.ports.repository import OrderRepoPort

CancelChild = Callable[[CancelOrderCommand], Awaitable[OrderView]]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def cancel_algo(
    *,
    pool: asyncpg.Pool,
    orders_repo: OrderRepoPort,
    parent_order_id: UUID,
    tenant_id: UUID,
    reason: str,
    actor_subject_id: UUID | Literal["system"],
    cancel_child: CancelChild,
    now: datetime | None = None,
) -> list[OrderView]:
    """Cancel every currently-open child of `parent_order_id`, in the order
    `children_awaiting_cancel` returns them, and return the resulting
    `OrderView`s (one per successfully cancelled child).
    """
    occurred_at = now if now is not None else _utcnow()
    async with pool.acquire() as conn, conn.transaction():
        open_child_ids = await children_awaiting_cancel(orders_repo, conn, parent_order_id)

    results: list[OrderView] = []
    for child_id in open_child_ids:
        command = CancelOrderCommand(
            command_id=uuid4(),
            trace_id=parent_order_id,
            order_id=child_id,
            tenant_id=tenant_id,
            reason=reason,
            actor_subject_id=actor_subject_id,
            issued_at=occurred_at,
        )
        results.append(await cancel_child(command))
    return results
