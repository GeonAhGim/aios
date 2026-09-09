"""EM-3 -- application/aggregate_parent.py: wire EM-2's pure rollup/
propagation rules (`domain/parent_child.py`) into the canonical store.

Spec: docs/specs/L4_ems_routing_algos_and_tca_v1.0.md #2 module table, #9
EM-3 (task-2121 decision, 2026-09-06 audit: no new `parent_orders`/
`child_orders` tables). `orders.parent_order_id`/`algo_run_id` are the only
identity, and `orders.committed_child_qty`/`filled_quantity`/`status` are
the only aggregate storage -- this module reads/writes them through the
OMS `OrderRepoPort` (`src/services/oms/ports/repository.py`), the same
port `submit_order` (L4-09) uses, because EMS does not own a competing
`orders` adapter (2026-09-06 audit, same reasoning as EM-6's
`route_decisions`-only adapter).

Two primitives a future algo-tick scheduler (EM-15, out of this leaf's
scope per the module table) wires around `submit_order`:

- `reserve_child_slice` / `release_reserved_slice` -- EM-A1/EM-A4 applied
  *before* a child is submitted. Reservation and submission are
  deliberately two separate calls (spec #3 "the algorithm never calls an
  adapter directly" -- this module never calls `submit_order` either): if
  `submit_order`'s CM-8 gate later denies the reserved slice, EM-15 must
  call `release_reserved_slice` to undo the reservation. This leaf only
  supplies the atomic primitives, not that lifecycle wiring.
- `recompute_parent_aggregate` -- applied *after* a child fill/terminal
  transition lands, to roll the parent's own `status`/`filled_quantity`
  up from its children (EM-2 `aggregate_parent_state`). It never bypasses
  the state machine: when the rollup changes the parent's status, it goes
  through the identical `OrderRepoPort.transition()` path every other
  order-status change uses (I5 version bump, I6 `order_events` row) --
  aggregation writes are not a side channel.

`children_awaiting_cancel` exposes EM-2's cancel-propagation list (EM-A4)
for EM-15's `cancel_algo` to consume.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from decimal import Decimal
from uuid import UUID

import asyncpg

from src.data.models.trading import OrderStatus
from src.foundation.ems.domain.parent_child import (
    ChildFillState,
    aggregate_parent_state,
    assert_can_create_child,
    children_pending_cancellation,
)
from src.services.oms.contracts.v1_events import OrderTransitionEvent
from src.services.oms.contracts.v1_views import OrderView
from src.services.oms.domain.state_machine import OrderEvent
from src.services.oms.ports.repository import OrderRepoPort

_AGGREGATE_EVENT: dict[OrderStatus, OrderEvent] = {
    OrderStatus.FILLED: OrderEvent.FILL,
    OrderStatus.PARTIALLY_FILLED: OrderEvent.FILL,
    OrderStatus.CANCELLED: OrderEvent.VENUE_CANCELLED,
}
"""aggregate_parent_state()'s three possible non-noop destinations, mapped
to the event name recorded in order_events -- FILL covers both partial and
full rollups (the state machine distinguishes the two by the destination
status, not the event name, same as the rest of L4-02)."""


async def reserve_child_slice(
    orders_repo: OrderRepoPort,
    conn: asyncpg.Connection,
    *,
    parent_order_id: UUID,
    new_slice_qty: Decimal,
) -> OrderView:
    """EM-A1/EM-A4 -- lock the parent, apply EM-2's combinator against the
    materialized `committed_child_qty`, then commit the accepted slice.

    Raises `ParentTerminalError`/`AlgoConstraintError` fail-closed (parent
    row still locked in `conn`'s transaction, nothing written) -- the
    caller must not proceed to `submit_order` if this raises.
    """
    parent = await orders_repo.get_for_update(conn, parent_order_id)
    assert_can_create_child(
        parent.status, parent.quantity, parent.committed_child_qty, new_slice_qty
    )
    return await orders_repo.set_committed_child_qty(
        conn,
        parent_order_id=parent_order_id,
        expected_version=parent.version,
        committed_child_qty=parent.committed_child_qty + new_slice_qty,
    )


async def release_reserved_slice(
    orders_repo: OrderRepoPort,
    conn: asyncpg.Connection,
    *,
    parent_order_id: UUID,
    slice_qty: Decimal,
) -> OrderView:
    """Undo `reserve_child_slice` when the reserved slice never became a
    submitted child (e.g. `submit_order`'s CM-8 gate denied it)."""
    parent = await orders_repo.get_for_update(conn, parent_order_id)
    released = parent.committed_child_qty - slice_qty
    if released < 0:
        raise ValueError(
            f"parent {parent_order_id}: releasing {slice_qty} would bring "
            f"committed_child_qty below 0 (currently {parent.committed_child_qty}) "
            "-- caller is releasing more than it ever reserved."
        )
    return await orders_repo.set_committed_child_qty(
        conn,
        parent_order_id=parent_order_id,
        expected_version=parent.version,
        committed_child_qty=released,
    )


def _aggregate_payload_hash(parent_order_id: UUID, filled_qty: Decimal, status: OrderStatus) -> str:
    canonical = json.dumps(
        {
            "parent_order_id": str(parent_order_id),
            "filled_qty": str(filled_qty),
            "status": status.value,
        },
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


async def recompute_parent_aggregate(
    orders_repo: OrderRepoPort,
    conn: asyncpg.Connection,
    *,
    parent_order_id: UUID,
    trace_id: UUID,
    occurred_at: datetime,
) -> OrderView:
    """EM-2 rollup, applied. Locks the parent and every child, recomputes
    `(filled_qty, status)` via `aggregate_parent_state`, and if the status
    actually moves, drives it through `OrderRepoPort.transition()` -- the
    same path `submit_order`/fill-processing use, so I5/I6 apply here too.

    A no-op rollup (no children yet, or open children with zero fill) makes
    no write and returns the parent unchanged -- `transition()` is never
    called with `new_status == parent.status` (not a valid pair for most
    statuses, and pointless even where it would be, e.g. PARTIALLY_FILLED
    self-loop -- EM-2 only returns that when there's nothing new to report).
    """
    parent = await orders_repo.get_for_update(conn, parent_order_id)
    children = await orders_repo.list_children_for_update(conn, parent_order_id)
    filled_qty, new_status = aggregate_parent_state(
        parent.quantity,
        parent.status,
        [
            ChildFillState(child_id=c.order_id, filled_qty=c.filled_quantity, status=c.status)
            for c in children
        ],
    )
    if new_status == parent.status:
        return parent

    event_name = _AGGREGATE_EVENT.get(new_status)
    if event_name is None:  # pragma: no cover -- aggregate_parent_state's contract
        raise ValueError(
            f"aggregate_parent_state produced {new_status} for parent {parent_order_id}, "
            "which recompute_parent_aggregate does not know how to record."
        )

    event = OrderTransitionEvent(
        order_id=parent_order_id,
        from_status=parent.status,
        to_status=new_status,
        event=event_name.value,
        reason_code="EM3_CHILD_AGGREGATE",
        actor_subject_id="system",
        trace_id=trace_id,
        command_id=None,
        provider_event_id=None,
        occurred_at=occurred_at,
        payload_hash=_aggregate_payload_hash(parent_order_id, filled_qty, new_status),
    )
    return await orders_repo.transition(
        conn,
        order_id=parent_order_id,
        expected_status=parent.status,
        expected_version=parent.version,
        new_status=new_status,
        patch={"filled_quantity": filled_qty},
        event=event,
    )


async def children_awaiting_cancel(
    orders_repo: OrderRepoPort, conn: asyncpg.Connection, parent_order_id: UUID
) -> list[UUID]:
    """EM-A4 propagation -- open child `order_id`s a parent cancel must
    still reach (EM-15's `cancel_algo` submits CANCEL for each)."""
    children = await orders_repo.list_children_for_update(conn, parent_order_id)
    return children_pending_cancellation(
        [
            ChildFillState(child_id=c.order_id, filled_qty=c.filled_quantity, status=c.status)
            for c in children
        ]
    )
