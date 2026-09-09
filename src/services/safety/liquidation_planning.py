"""R-52 -- liquidation_executor.py's REQUESTED -> PLANNED half.

Spec: docs/specs/L4_risk_and_safety_v1.0.md#3.7 (`liquidation_planner`), §4
`liquidation_request` row 431, §9 R-52 (task-2358). Split out of
`liquidation_executor.py` to keep each module under the P6 300-line cap
(the combined worker -- candidate selection, planning, slice execution,
deadline fallback, completion -- doesn't fit one 220-300 line file); this
module owns "turn a REQUESTED request into slices + a PLANNED plan",
`liquidation_executor.py` owns everything after that.

Honest scope reductions (shared with `liquidation_executor.py`, repeated
here since this is where they take effect): a symbol held on positions
across more than one exchange at once can't be represented by a single
`liquidation_slice` row (schema is symbol-only, no per-exchange column) and
is skipped, logged; lot size is approximated from
`ExchangeCapability.min_order_size` rather than a real instrument lookup.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from collections import defaultdict
from collections.abc import Mapping
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID

import asyncpg

from src.core.db.conditional_write import conditional_update
from src.core.loader.risk_policy_loader import load_risk_policy
from src.core.logging.audit_log import record_audit_log
from src.core.safety.liquidation_planner import OpenPosition, plan_liquidation
from src.data.models.trading import OrderSide
from src.exchanges.common.adapter import ExchangeAdapter

logger = logging.getLogger(__name__)

SEED_KEY_ENV = "AIOS_LIQUIDATION_SEED_KEY"
_DEFAULT_LOT_SIZE = Decimal("0.0001")


class LiquidationSeedKeyMissingError(RuntimeError):
    """§10 -- fail-closed if the HMAC seed secret isn't configured."""


def require_seed_key() -> bytes:
    value = os.environ.get(SEED_KEY_ENV)
    if not value:
        raise LiquidationSeedKeyMissingError(f"{SEED_KEY_ENV} 미설정 -- fail-closed 거부")
    return value.encode()


async def load_positions(
    conn: asyncpg.Connection, scope: str, scope_ref: str, adapters: Mapping[str, ExchangeAdapter]
) -> tuple[list[OpenPosition], dict[str, str], dict[str, str]]:
    """Open positions aggregated by symbol (net across every account -- see
    module docstring). Returns the planner input plus the exchange/side
    each symbol resolved to, so the executor doesn't have to re-derive
    them (and can't race against positions changing mid-liquidation)."""
    where, params = "quantity <> 0 AND closed_at IS NULL", []
    if scope == "ACCOUNT":
        where += " AND user_id = $1"
        params.append(UUID(scope_ref))
    rows = await conn.fetch(
        f"SELECT symbol, exchange, SUM(quantity) AS net_qty FROM positions "  # noqa: S608
        f"WHERE {where} GROUP BY symbol, exchange",
        *params,
    )
    by_symbol: dict[str, list[tuple[str, Decimal]]] = defaultdict(list)
    for r in rows:
        by_symbol[r["symbol"]].append((r["exchange"], r["net_qty"]))

    positions: list[OpenPosition] = []
    exchange_by_symbol: dict[str, str] = {}
    side_by_symbol: dict[str, str] = {}
    for symbol, entries in by_symbol.items():
        if len(entries) != 1:
            logger.warning(
                "liquidation_planning: %s spans %d exchanges, skipped", symbol, len(entries)
            )
            continue
        exchange, net_qty = entries[0]
        if net_qty == 0:
            continue
        adapter = adapters.get(exchange)
        if adapter is None:
            logger.warning("liquidation_planning: no adapter for %s, %s skipped", exchange, symbol)
            continue
        try:
            ticker = await adapter.get_ticker(symbol)
        except Exception:  # noqa: BLE001 -- one symbol's failure must not block the whole plan
            logger.exception("liquidation_planning: ticker fetch failed for %s, skipped", symbol)
            continue
        lot_size = adapter.get_capabilities().min_order_size.get(symbol, _DEFAULT_LOT_SIZE)
        quantity = abs(net_qty)
        notional = quantity * ticker.price
        positions.append(
            OpenPosition(symbol=symbol, quantity=quantity, notional=notional, lot_size=lot_size)
        )
        exchange_by_symbol[symbol] = exchange
        side_by_symbol[symbol] = (OrderSide.SELL if net_qty > 0 else OrderSide.BUY).value
    return positions, exchange_by_symbol, side_by_symbol


async def plan_request(
    pool: asyncpg.Pool, adapters: Mapping[str, ExchangeAdapter], row: asyncpg.Record,
    seed_key: bytes, now: datetime,
) -> None:
    """§4 row 431 -- REQUESTED -> PLANNED (or straight to DONE if nothing is
    open to liquidate): `plan_liquidation(seed=HMAC(secret, request_id))`,
    plan JSONB + slices INSERT."""
    async with pool.acquire() as conn:
        positions, exchange_by_symbol, side_by_symbol = await load_positions(
            conn, row["scope"], row["scope_ref"], adapters
        )
    if not positions:
        async with pool.acquire() as conn, conn.transaction():
            await conditional_update(
                conn, table="liquidation_request", id_column="id", id_value=row["id"],
                expected_state_column="state", expected_state_value="REQUESTED",
                set_values={"state": "DONE", "completed_at": now},
            )
            await record_audit_log(
                conn, actor_agent="liquidation_executor", action_type="liquidation_completed",
                decision_data={
                    "request_id": str(row["id"]), "state": "DONE", "reason": "no_open_positions"
                },
                target_type="liquidation_request", target_id=str(row["id"]),
            )
        return

    seed = hmac.new(seed_key, str(row["id"]).encode(), hashlib.sha256).digest()
    policy = load_risk_policy().liquidation
    plan = plan_liquidation(positions=positions, seed=seed, policy=policy, volume_5m={})
    plan_json = json.dumps({
        "plan": plan.model_dump(mode="json"),
        "exchange_by_symbol": exchange_by_symbol,
        "side_by_symbol": side_by_symbol,
    })
    async with pool.acquire() as conn, conn.transaction():
        for s in plan.slices:
            not_before = now + timedelta(seconds=s.not_before_offset_sec)
            await conn.execute(
                "INSERT INTO liquidation_slice "
                "(request_id, seq, symbol, quantity, not_before, state) "
                "VALUES ($1,$2,$3,$4,$5,'PENDING')",
                row["id"], s.seq, s.symbol, s.quantity, not_before,
            )
        await conditional_update(
            conn, table="liquidation_request", id_column="id", id_value=row["id"],
            expected_state_column="state", expected_state_value="REQUESTED",
            set_values={"state": "PLANNED", "plan": plan_json, "seed_ref": plan.seed_hash},
        )
        await record_audit_log(
            conn, actor_agent="liquidation_executor", action_type="liquidation_planned",
            decision_data={
                "request_id": str(row["id"]), "slice_count": len(plan.slices),
                "seed_hash": plan.seed_hash,
            },
            target_type="liquidation_request", target_id=str(row["id"]),
        )
