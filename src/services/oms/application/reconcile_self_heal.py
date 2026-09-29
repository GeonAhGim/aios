"""Self-heal helper for `three_way_reconciler.py` (task-7978 F1, liveness fix).

Split out of `three_way_reconciler.py` solely to stay under the architecture
guard's `P6.line_cap` (see `docs/design/*ADR*` file-splitting precedent) — this
module has no independent responsibility of its own; it is reconciler-internal
plumbing.

A venue-confirmed cancel (no fill, the order simply drops out of
`get_open_orders()`) was never wired to `OrderEvent.VENUE_CANCELLED` anywhere
in OMS — `inbox_processor.py` only transitions on fills and drops no-fill
provider events as IGNORED. That made the reconciler classify a perfectly
normal cancel as `ORDER_MISSING_AT_PROVIDER`/`MATERIAL_MISMATCH` forever,
permanently DENYing every new SUBMIT for the tenant. `self_heal_confirmed_cancels`
closes that loop narrowly, here only: an order classified
`ORDER_MISSING_AT_PROVIDER` that also has a `CANCEL_REQUESTED` event in its
`order_events` history is transitioned to `VENUE_CANCELLED` and dropped from
the discrepancy list before the gate decision, instead of adding a new
inbox-event consumption path. Orders missing at the provider with no
`CANCEL_REQUESTED` history (real loss, e.g. a missed fill) are left as
`MATERIAL_MISMATCH` — "missing" alone is never treated as "cancelled".
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from uuid import UUID, uuid4

import asyncpg

from src.core.db.conditional_write import ConcurrencyConflictError
from src.services.oms.adapters.order_events_repository import PostgresOrderEventRepository
from src.services.oms.adapters.order_repository import OrderNotFoundError, PostgresOrderRepository
from src.services.oms.contracts.v1_events import Discrepancy, OrderTransitionEvent
from src.services.oms.domain.errors import InvalidOrderTransitionError
from src.services.oms.domain.state_machine import OrderEvent, next_status

logger = logging.getLogger(__name__)

_orders_repo = PostgresOrderRepository()
_order_events_repo = PostgresOrderEventRepository()
_SELF_HEAL_REASON = "RECONCILE_SELF_HEAL_VENUE_CANCELLED"


def _venue_cancel_event_hash(order_id: UUID, occurred_at: datetime) -> str:
    canonical = json.dumps(
        {
            "order_id": str(order_id),
            "event": OrderEvent.VENUE_CANCELLED.value,
            "occurred_at": occurred_at.isoformat(),
        },
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


async def _heal_confirmed_cancel(conn: asyncpg.Connection, order_id: UUID) -> bool:
    """Transitions `order_id` to `VENUE_CANCELLED` when it is classified
    `ORDER_MISSING_AT_PROVIDER` but is actually a normal venue-confirmed cancel
    (dropped out of open-orders because the venue confirmed the cancel).

    Orders without a `CANCEL_REQUESTED` history are left untouched — treating
    every missing order as CANCELLED would launder real losses (e.g. a missed
    fill) into a benign cancel (task-7978 F1, narrow self-heal scope)."""
    timeline = await _order_events_repo.timeline(conn, order_id)
    if not any(ev.event == OrderEvent.CANCEL_REQUESTED.value for ev in timeline):
        return False

    tx = conn.transaction()
    await tx.start()
    healed = False
    try:
        order = await _orders_repo.get_for_update(conn, order_id)
        new_status = next_status(order.status, OrderEvent.VENUE_CANCELLED)
        occurred_at = datetime.now(timezone.utc)
        event = OrderTransitionEvent(
            order_id=order_id,
            from_status=order.status,
            to_status=new_status,
            event=OrderEvent.VENUE_CANCELLED.value,
            reason_code=_SELF_HEAL_REASON,
            actor_subject_id="system",
            trace_id=uuid4(),
            command_id=None,
            provider_event_id=None,
            occurred_at=occurred_at,
            payload_hash=_venue_cancel_event_hash(order_id, occurred_at),
        )
        await _orders_repo.transition(
            conn,
            order_id=order_id,
            expected_status=order.status,
            expected_version=order.version,
            new_status=new_status,
            patch={},
            event=event,
        )
        healed = True
    except (InvalidOrderTransitionError, ConcurrencyConflictError, OrderNotFoundError):
        logger.warning(
            "reconcile self-heal: skipping VENUE_CANCELLED transition for order_id=%s",
            order_id,
            exc_info=True,
        )
    finally:
        if healed:
            await tx.commit()
        else:
            await tx.rollback()
    return healed


async def self_heal_confirmed_cancels(
    pool: asyncpg.Pool, discrepancies: list[Discrepancy]
) -> list[Discrepancy]:
    """Among discrepancies classified `ORDER_MISSING_AT_PROVIDER`, transitions
    only the ones with a `CANCEL_REQUESTED` history to `VENUE_CANCELLED`, and
    drops the healed entries from the result so this run's classification
    (and the resulting ACCOUNT gate decision) reflects the heal (task-7978 F1)."""
    missing_ids = {d.entity_key for d in discrepancies if d.kind == "ORDER_MISSING_AT_PROVIDER"}
    if not missing_ids:
        return discrepancies

    healed: set[str] = set()
    for order_id_str in missing_ids:
        async with pool.acquire() as conn:
            if await _heal_confirmed_cancel(conn, UUID(order_id_str)):
                healed.add(order_id_str)

    if not healed:
        return discrepancies
    return [
        d
        for d in discrepancies
        if not (d.kind == "ORDER_MISSING_AT_PROVIDER" and d.entity_key in healed)
    ]
