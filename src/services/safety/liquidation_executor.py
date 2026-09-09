"""R-52 -- split-liquidation slice execution worker.

Spec: docs/specs/L4_risk_and_safety_v1.0.md#3.7, §4 `liquidation_request`
state rows 426-435, §5 row 454 (SELECT...FOR UPDATE SKIP LOCKED), §9 R-52
(task-2358, depends on R-51/task-2357). Frozen contract (§3 row 142):
`run_liquidation_worker_once(pool, adapters, *, now)`. The REQUESTED ->
PLANNED step (position sourcing, `plan_liquidation`, slice INSERT) lives in
`liquidation_planning.py` (split to keep both modules under the P6 300-line
cap -- see that module's docstring); this module owns everything from
PLANNED onward: fence-change ABORT, slice/fallback sending, completion.

One call advances exactly one non-terminal `liquidation_request` by one step
(ABORT on fence change, plan, or one slice/fallback send); the caller
re-invokes this periodically to drain the queue -- same "pick one
candidate, do one thing" shape as `open_order_sweeper.py`.

Honest scope reductions: (1) `liquidation_slice` has no per-user column
(§5 schema is symbol-only) -- positions are aggregated by symbol across
every account and executed under the same "borrowed system identity"
convention `watchdog_process.py` uses for GLOBAL actions with no single
owning tenant. (2) `adverse_move_abort_pct` (§4 row 434's other trigger)
isn't evaluated -- only the DoD-required `deadline` fallback is. (3)
`slice_ttl_sec` isn't enforced (no TTL param on `Order`/`place_order`).
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from collections.abc import Mapping
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID

import asyncpg

from src.core.db.conditional_write import conditional_update
from src.core.logging.audit_log import record_audit_log
from src.data.models.base import AssetClass, Currency, Money
from src.data.models.trading import Order, OrderSide, OrderStatus, OrderType
from src.exchanges.common.adapter import ExchangeAdapter
from src.services.safety.liquidation_planning import plan_request, require_seed_key

logger = logging.getLogger(__name__)

_NON_TERMINAL = ("REQUESTED", "PLANNED", "EXECUTING")
# = watchdog_process.WATCHDOG_SYSTEM_ACTOR_ID / e5a8c5d4f6b7's seeded users
# row -- redefined locally (same constant-parity-check convention that
# migration's docstring uses) since slices carry no per-user owner (see
# module docstring (1)).
_SYSTEM_ACTOR_ID = UUID("00000000-0000-0000-0000-000000000002")


async def _select_candidate(pool: asyncpg.Pool) -> asyncpg.Record | None:
    # `states` is this module's own constant tuple, never user input.
    states = ", ".join(f"'{s}'" for s in _NON_TERMINAL)
    query = (
        "SELECT lr.id, lr.scope, lr.scope_ref, lr.state, lr.fence_token, lr.requested_at, "  # noqa: S608
        "lr.plan, sf.current_token AS live_fence FROM liquidation_request lr "
        "JOIN safety_fence sf ON sf.scope = lr.scope AND sf.scope_ref = lr.scope_ref "
        f"WHERE lr.state IN ({states}) "
        "ORDER BY lr.requested_at FOR UPDATE OF lr SKIP LOCKED LIMIT 1"
    )
    async with pool.acquire() as conn, conn.transaction():
        return await conn.fetchrow(query)


async def _abort(pool: asyncpg.Pool, row: asyncpg.Record, now: datetime) -> None:
    """§4 row 435 -- a newer control superseded this request's fence."""
    async with pool.acquire() as conn, conn.transaction():
        await conditional_update(
            conn, table="liquidation_request", id_column="id", id_value=row["id"],
            expected_state_column="state", expected_state_value=row["state"],
            set_values={"state": "ABORTED", "completed_at": now},
        )
        await conn.execute(
            "UPDATE liquidation_slice SET state='SKIPPED' WHERE request_id=$1 AND state='PENDING'",
            row["id"],
        )
        await record_audit_log(
            conn, actor_agent="liquidation_executor", action_type="liquidation_aborted",
            decision_data={"request_id": str(row["id"]), "reason": "fence_changed"},
            target_type="liquidation_request", target_id=str(row["id"]),
        )


async def _submit_order(
    pool: asyncpg.Pool, adapters: Mapping[str, ExchangeAdapter], request_id: UUID, *,
    symbol: str, exchange: str, side: str, quantity: Decimal, order_type: OrderType,
    tolerance_bps: Decimal, slice_ids: list[int], event: str,
) -> None:
    """One adapter call outside any DB transaction (§2-B P1 -- never hold a
    connection across network I/O); a short transaction then persists the
    order (I1: `is_liquidation=TRUE` + `liquidation_request_id`) and
    finalizes every slice it covers (>1 only for the deadline fallback,
    where several PENDING slices consolidate into one MARKET order)."""
    adapter = adapters.get(exchange)
    price: Money | None = None
    if adapter is not None and order_type == OrderType.LIMIT:
        ticker = await adapter.get_ticker(symbol)
        mid = (ticker.bid + ticker.ask) / 2
        adjustment = mid * (tolerance_bps / Decimal(10000))
        price = Money(
            amount=mid - adjustment if side == OrderSide.SELL.value else mid + adjustment,
            currency=Currency.USDT,
        )
    order = Order(
        client_order_id=f"liq:{request_id}:{event}:{symbol}:{slice_ids[0]}",
        strategy_id="liquidation", strategy_version="1", symbol=symbol, exchange=exchange,
        side=OrderSide(side), order_type=order_type, quantity=quantity, price=price,
        is_liquidation=True, asset_class=AssetClass.CRYPTO,
    )
    status = OrderStatus.FAILED
    if adapter is not None:
        try:
            status = (await adapter.place_order(order)).status
        except Exception:  # noqa: BLE001 -- one slice's failure must not kill the worker loop
            logger.exception("liquidation_executor: place_order failed: %s", order.client_order_id)
    new_state = "FAILED" if status == OrderStatus.FAILED else "SENT"
    insert_orders_sql = (
        "INSERT INTO orders (order_id, user_id, client_order_id, strategy_id, strategy_version, "
        "symbol, exchange, side, order_type, quantity, price, status, is_liquidation, "
        "asset_class, liquidation_request_id) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,TRUE,$13,$14)"
    )
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            insert_orders_sql,
            order.order_id, _SYSTEM_ACTOR_ID, order.client_order_id, order.strategy_id,
            order.strategy_version, order.symbol, order.exchange, order.side.value,
            order.order_type.value, order.quantity,
            order.price.amount if order.price is not None else None,
            status.value, order.asset_class.value, request_id,
        )
        await conn.execute(
            "UPDATE liquidation_slice SET state=$1, order_id=$2 WHERE id = ANY($3::bigint[])",
            new_state, order.order_id, slice_ids,
        )
        event_type = (
            "liquidation_fallback_market" if event == "fallback" else "liquidation_slice_sent"
        )
        await record_audit_log(
            conn, actor_agent="liquidation_executor", action_type=event_type,
            decision_data={
                "request_id": str(request_id), "symbol": symbol, "quantity": str(quantity),
                "status": status.value, "slice_ids": slice_ids,
            },
            target_type="liquidation_request", target_id=str(request_id),
        )


async def _finish_if_drained(
    conn: asyncpg.Connection, request_id: UUID, current_state: str, now: datetime
) -> None:
    """§4 row 433 -- ended here means this worker's own placement outcome
    (SENT/FAILED/SKIPPED), not exchange fill confirmation; SENT->FILLED
    reconciliation is an existing, separate OMS concern once orders exist."""
    remaining = await conn.fetchval(
        "SELECT COUNT(*) FROM liquidation_slice WHERE request_id=$1 AND state='PENDING'", request_id
    )
    if remaining != 0:
        return
    failed = await conn.fetchval(
        "SELECT EXISTS(SELECT 1 FROM liquidation_slice WHERE request_id=$1 AND state='FAILED')",
        request_id,
    )
    new_state = "PARTIAL" if failed else "DONE"
    await conditional_update(
        conn, table="liquidation_request", id_column="id", id_value=request_id,
        expected_state_column="state", expected_state_value=current_state,
        set_values={"state": new_state, "completed_at": now},
    )
    await record_audit_log(
        conn, actor_agent="liquidation_executor", action_type="liquidation_completed",
        decision_data={"request_id": str(request_id), "state": new_state},
        target_type="liquidation_request", target_id=str(request_id),
    )


async def _fallback_market(
    pool: asyncpg.Pool, adapters: Mapping[str, ExchangeAdapter], row: asyncpg.Record,
    exchange_by_symbol: dict[str, str], side_by_symbol: dict[str, str],
) -> None:
    """§4 row 434 -- deadline exceeded: claim every remaining PENDING slice
    (-> SENT, so a second tick can't re-claim it) before any adapter call,
    then send one consolidated MARKET order per symbol."""
    async with pool.acquire() as conn, conn.transaction():
        pending = await conn.fetch(
            "SELECT id, symbol, quantity FROM liquidation_slice "
            "WHERE request_id=$1 AND state='PENDING' FOR UPDATE SKIP LOCKED",
            row["id"],
        )
        if pending:
            await conn.execute(
                "UPDATE liquidation_slice SET state='SENT' WHERE id = ANY($1::bigint[])",
                [r["id"] for r in pending],
            )
    by_symbol: dict[str, list[asyncpg.Record]] = defaultdict(list)
    for r in pending:
        by_symbol[r["symbol"]].append(r)
    for symbol, slices in by_symbol.items():
        await _submit_order(
            pool, adapters, row["id"], symbol=symbol, exchange=exchange_by_symbol[symbol],
            side=side_by_symbol[symbol], quantity=sum((s["quantity"] for s in slices), Decimal(0)),
            order_type=OrderType.MARKET, tolerance_bps=Decimal(0),
            slice_ids=[s["id"] for s in slices], event="fallback",
        )


async def _advance(
    pool: asyncpg.Pool, adapters: Mapping[str, ExchangeAdapter], row: asyncpg.Record, now: datetime
) -> None:
    """§4 row 432 -- PLANNED/EXECUTING: send the next due slice, or the
    deadline fallback (row 434), or finalize once nothing is left PENDING."""
    plan_data = json.loads(row["plan"])
    plan_meta = plan_data["plan"]
    deadline_at = row["requested_at"] + timedelta(seconds=plan_meta["deadline_offset_sec"])
    exchange_by_symbol = plan_data["exchange_by_symbol"]
    side_by_symbol = plan_data["side_by_symbol"]

    if now >= deadline_at:
        await _fallback_market(pool, adapters, row, exchange_by_symbol, side_by_symbol)
        async with pool.acquire() as conn, conn.transaction():
            await _finish_if_drained(conn, row["id"], row["state"], now)
        return

    async with pool.acquire() as conn, conn.transaction():
        due = await conn.fetchrow(
            "SELECT id, seq, symbol, quantity FROM liquidation_slice WHERE request_id=$1 "
            "AND state='PENDING' AND (not_before IS NULL OR not_before <= $2) "
            "ORDER BY seq FOR UPDATE SKIP LOCKED LIMIT 1",
            row["id"], now,
        )
        if due is None:
            await _finish_if_drained(conn, row["id"], row["state"], now)
            return
        await conditional_update(
            conn, table="liquidation_slice", id_column="id", id_value=due["id"],
            expected_state_column="state", expected_state_value="PENDING",
            set_values={"state": "SENT"},
        )
        if row["state"] == "PLANNED":
            await conditional_update(
                conn, table="liquidation_request", id_column="id", id_value=row["id"],
                expected_state_column="state", expected_state_value="PLANNED",
                set_values={"state": "EXECUTING"},
            )

    order_meta = next(s for s in plan_meta["slices"] if s["seq"] == due["seq"])
    await _submit_order(
        pool, adapters, row["id"], symbol=due["symbol"], exchange=exchange_by_symbol[due["symbol"]],
        side=side_by_symbol[due["symbol"]], quantity=due["quantity"],
        order_type=OrderType(order_meta["order_type"]),
        tolerance_bps=Decimal(order_meta["limit_tolerance_bps"]),
        slice_ids=[due["id"]], event="slice",
    )


async def run_liquidation_worker_once(
    pool: asyncpg.Pool, adapters: Mapping[str, ExchangeAdapter], *, now: datetime
) -> None:
    """§3 table row 142 (frozen public contract) -- advances exactly one
    non-terminal `liquidation_request` by one step per call."""
    seed_key = require_seed_key()
    row = await _select_candidate(pool)
    if row is None:
        return
    if row["live_fence"] != row["fence_token"]:
        await _abort(pool, row, now)
        return
    if row["state"] == "REQUESTED":
        await plan_request(pool, adapters, row, seed_key, now)
        return
    await _advance(pool, adapters, row, now)
