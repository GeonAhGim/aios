"""4.6 — Restart recovery procedure.

Spec: 05_communication_architecture_v1.2.md#§5.6

Deviation: This procedure originally requires orders table lookup (DB session
layer, workflow tree #16) and ExchangeAdapter.get_order() (workflow tree #6),
neither of which exist yet at this point (workflow tree #4). It is designed as
a pure orchestration function that receives these three via callback injection —
when the relevant sections are complete, only the actual implementation needs
to be plugged in (same pattern as the §5.1 principle of hiding the Event Bus
behind its interface).
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)

# Return rows where orders.status has not yet reached a terminal state
# (FILLED/CANCELLED/REJECTED/EXPIRED/FAILED) — target for reusing the same
# query logic as §7.5 UNKNOWN handling principle.
FetchPendingOrders = Callable[[], Awaitable[list[dict[str, Any]]]]
GetOrderStatus = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
RepublishOrderEvent = Callable[[dict[str, Any]], Awaitable[None]]
RecordRecovery = Callable[[int], Awaitable[None]]


async def recover_pending_orders(
    *,
    fetch_pending_orders: FetchPendingOrders,
    get_order_status: GetOrderStatus,
    republish_order_event: RepublishOrderEvent,
    record_recovery: RecordRecovery | None = None,
) -> int:
    """Called once at process startup.

    §5.6 principle — "DB writes happen before event publication. Even if an
    event is lost, the truth always resides in the DB" — republishes events
    that may have been lost on restart, based on DB state. Returns the count
    of re-synchronized orders (audit trail, §5.6 — "always track how many
    orders were re-synchronized after restart").
    """
    pending = await fetch_pending_orders()
    recovered = 0
    for order in pending:
        try:
            current = await get_order_status(order)
        except Exception:
            logger.exception(
                "Failed to reconfirm order status during restart recovery: order_id=%s", order.get("order_id")
            )
            continue
        await republish_order_event(current)
        recovered += 1

    logger.info("Restart recovery complete: %d orders re-synchronized", recovered)
    if record_recovery is not None:
        await record_recovery(recovered)
    return recovered
