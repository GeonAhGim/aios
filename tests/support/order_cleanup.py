"""Remove a test's own eventless order rows without breaking WORM tables.

Orders filled synchronously by `submit_order` carry `order_events` and `fills`
rows (task-16630). Both tables are append-only, so such an order cannot be
deleted -- and it does not need to be: its history replays to the stored row,
so the orders-residue sentinel treats it as consistent. Only eventless orders
(the real residue case) are deleted.
"""

from __future__ import annotations

import asyncpg


async def delete_orders_by_client_order_id(conn: asyncpg.Connection, client_order_id: str) -> bool:
    """Delete the order if it has no `order_events`/`fills`; return whether deleted."""
    row = await conn.fetchrow(
        "SELECT o.order_id, "
        "EXISTS (SELECT 1 FROM order_events e WHERE e.order_id = o.order_id) AS has_events, "
        "EXISTS (SELECT 1 FROM fills f WHERE f.order_id = o.order_id) AS has_fills "
        "FROM orders o WHERE o.client_order_id = $1",
        client_order_id,
    )
    if row is None or row["has_events"] or row["has_fills"]:
        return False
    await conn.execute("DELETE FROM orders WHERE order_id = $1", row["order_id"])
    return True


async def cleanup_liquidation_request(conn: asyncpg.Connection, request_id: object) -> None:
    """Tear down one `liquidation_request`'s rows (slice refs -> orders -> slices -> request).

    Liquidation orders now carry WORM `order_events` (task-18977), so they cannot be
    deleted; the request they reference is then kept, terminal (ABORTED)."""
    await conn.execute(
        "UPDATE liquidation_slice SET order_id = NULL WHERE request_id = $1", request_id
    )
    rows = await conn.fetch(
        "SELECT client_order_id FROM orders WHERE liquidation_request_id = $1", request_id
    )
    for r in rows:
        await delete_orders_by_client_order_id(conn, r["client_order_id"])
    await conn.execute("DELETE FROM liquidation_slice WHERE request_id = $1", request_id)
    if await conn.fetchval(
        "SELECT EXISTS (SELECT 1 FROM orders WHERE liquidation_request_id = $1)", request_id
    ):
        await conn.execute(
            "UPDATE liquidation_request SET state='ABORTED', completed_at=now() WHERE id = $1",
            request_id,
        )
    else:
        await conn.execute("DELETE FROM liquidation_request WHERE id = $1", request_id)
