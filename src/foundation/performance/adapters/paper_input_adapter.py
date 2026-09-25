"""PAPER scope `StatementInputPort` implementation.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md §2.6/§9(L48).

`scope_ref` is treated as the tenant's `user_id` (string) in this adapter —
since `orders`/`positions`/`strategy_executions` all express ownership via
`user_id` (boundary at §4, ticket 71; migration deviation 84b7d0faf14f —
at P0 scope tenant_id == user_id), having reconciliation(FND-08)'s
`target_ref` point to the same UUID lets all three contexts mesh on a
single key without a separate mapping table.

Limitations (explicit, scope-reduced — same reasoning as "no real ledger yet"
across tickets 71/80/81):
- `orders` has no `fee` column (see migration 210cc26533c7) — filled fees
  always remain unknown (PENDING). Do not fill with 0.
- `positions` holds only the current state and cannot reconstruct historical
  point-in-time snapshots (`valuation_snapshot` table (M5) is unused by
  anyone yet — that table is out of scope for this leaf). Therefore
  `load_reconciled_snapshots` always returns exactly 1 snapshot at the
  "now" point (call time) — it does not simulate a `period_start` value.
  Calculations requiring two boundary values like TWR/MWR should have
  compute_statement.py(L49) report the snapshot shortage as PENDING.
- `cash` is a derived value: `Σstrategy_executions.allocated_capital` −
  `Σ(open position quantity × average_entry_price)`. The caller must know
  this is an approximation, not a real cash ledger (hence alongside the
  two limitations above, leave `price_evidence=()` to explicitly state
  "no price evidence").
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

import asyncpg

from src.foundation.performance.domain.models import (
    Cashflow,
    CashflowKind,
    ValuationSnapshot,
    ValuationState,
)
from src.foundation.reconciliation.contracts.v1 import Classification

PERFORMANCE_RECONCILIATION_TARGET_TYPE = "paper_account"
"""reconciliation(FND-08) convention value for the `target_type` this context writes —
`target_ref` is the tenant's `user_id`."""

_TRUSTED_STATUSES = frozenset({Classification.HEALTHY, Classification.RESOLVED})


class UnreconciledInputError(Exception):
    """72 error taxonomy `INTEGRITY_STATEMENT_INPUT_UNRECONCILED` — router maps to 409.
The reconciliation state is either absent (never reconciled) or not
HEALTHY/RESOLVED (an active discrepancy exists), meaning this statement's
input cannot be trusted."""

    def __init__(self, scope_ref: str) -> None:
        super().__init__(f"INTEGRITY_STATEMENT_INPUT_UNRECONCILED: scope_ref={scope_ref}")
        self.reason_code = "INTEGRITY_STATEMENT_INPUT_UNRECONCILED"
        self.scope_ref = scope_ref


class PaperStatementInputAdapter:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def _latest_reconciliation_run_id(
        self, conn: asyncpg.Connection, tenant_id: UUID
    ) -> UUID | None:
        row = await conn.fetchrow(
            "SELECT id FROM reconciliation_run WHERE target_ref = $1 AND target_type = $2 "
            "ORDER BY created_at DESC LIMIT 1",
            tenant_id,
            PERFORMANCE_RECONCILIATION_TARGET_TYPE,
        )
        return row["id"] if row is not None else None

    async def load_reconciled_snapshots(
        self, *, scope_ref: str, period_start: datetime, period_end: datetime
    ) -> tuple[ValuationSnapshot, ...]:
        tenant_id = UUID(scope_ref)
        async with self._pool.acquire() as conn:
            state_row = await conn.fetchrow(
                "SELECT aggregate_status FROM reconciliation_state "
                "WHERE target_ref = $1 AND target_type = $2",
                tenant_id,
                PERFORMANCE_RECONCILIATION_TARGET_TYPE,
            )
            if state_row is None or Classification(state_row["aggregate_status"]) not in (
                _TRUSTED_STATUSES
            ):
                raise UnreconciledInputError(scope_ref)

            reconciliation_run_id = await self._latest_reconciliation_run_id(conn, tenant_id)

            position_rows = await conn.fetch(
                "SELECT p.symbol, p.exchange, p.quantity, p.average_entry_price, "
                "       p.unrealized_pnl, p.realized_pnl "
                "FROM positions p JOIN strategy_executions e ON e.id = p.execution_id "
                "WHERE p.user_id = $1 AND e.mode = 'PAPER' AND p.entry_time <= $3 "
                "AND (p.closed_at IS NULL OR p.closed_at >= $2)",
                tenant_id,
                period_start,
                period_end,
            )
            capital_row = await conn.fetchrow(
                "SELECT COALESCE(SUM(allocated_capital), 0) AS total FROM strategy_executions "
                "WHERE user_id = $1 AND mode = 'PAPER' AND created_at <= $2",
                tenant_id,
                period_end,
            )

        positions = tuple(
            {
                "symbol": r["symbol"],
                "exchange": r["exchange"],
                "quantity": str(r["quantity"]),
                "average_entry_price": str(r["average_entry_price"]),
                "unrealized_pnl": str(r["unrealized_pnl"]),
                "realized_pnl": str(r["realized_pnl"]),
            }
            for r in position_rows
        )
        deployed_notional = sum(
            (r["quantity"] * r["average_entry_price"] for r in position_rows), Decimal(0)
        )
        cash = capital_row["total"] - deployed_notional

        snapshot = ValuationSnapshot(
            id=uuid4(),
            tenant_id=tenant_id,
            scope="PAPER",
            scope_ref=scope_ref,
            as_of=period_end,
            positions=positions,
            cash=cash,
            price_evidence=(),
            reconciliation_run_id=reconciliation_run_id,
            state=ValuationState.RECONCILED,
        )
        return (snapshot,)

    async def load_fills(
        self, *, scope_ref: str, period_start: datetime, period_end: datetime
    ) -> tuple[dict[str, object], ...]:
        tenant_id = UUID(scope_ref)
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT o.order_id, o.symbol, o.exchange, o.side, o.filled_quantity, "
                "       o.average_fill_price, o.updated_at "
                "FROM orders o JOIN strategy_executions e ON e.id = o.execution_id "
                "WHERE o.user_id = $1 AND e.mode = 'PAPER' AND o.status = 'FILLED' "
                "AND o.updated_at >= $2 AND o.updated_at <= $3",
                tenant_id,
                period_start,
                period_end,
            )
        return tuple(
            {
                "order_id": str(r["order_id"]),
                "symbol": r["symbol"],
                "exchange": r["exchange"],
                "side": r["side"],
                "filled_quantity": str(r["filled_quantity"]),
                "average_fill_price": (
                    str(r["average_fill_price"]) if r["average_fill_price"] is not None else None
                ),
                # no fee column in orders — always PENDING (see module docstring above)
                "fee": None,
                "at": r["updated_at"].isoformat(),
            }
            for r in rows
        )

    async def load_cashflows(
        self, *, scope_ref: str, period_start: datetime, period_end: datetime
    ) -> tuple[Cashflow, ...]:
        tenant_id = UUID(scope_ref)
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT allocated_capital, started_at FROM strategy_executions "
                "WHERE user_id = $1 AND mode = 'PAPER' AND started_at IS NOT NULL "
                "AND started_at >= $2 AND started_at <= $3",
                tenant_id,
                period_start,
                period_end,
            )
        return tuple(
            Cashflow(at=r["started_at"], amount=r["allocated_capital"], kind=CashflowKind.DEPOSIT)
            for r in rows
        )
