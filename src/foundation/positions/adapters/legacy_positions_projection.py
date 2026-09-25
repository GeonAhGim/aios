"""LB-10 — Project `pos_snapshot` into legacy `positions` query shape.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9.3 LB-10.

task-376 decision: "Not FROZEN — proceed as-is. ... Do not touch write
paths — read-only projection." The `project(conn, snap)` (snapshot →
legacy row upsert) described in §2.3 table is not implemented here — that
write path transition belongs to LB-12. This leaf is a read-only adapter
that reads `pos_snapshot` (already populated by LB-9
`postgres_snapshot_repository.py`) in the same shape that three existing
services (`risk_guard_service.py`, `portfolio_service.py`,
`report_service.py`) read directly from the `positions` table, proving
that new and legacy query paths yield identical results.

Row mapping is fixed via `pos_snapshot.legacy_position_id` (FK
`positions(id)`, LB-8) (§9 R10) — no one writes this column yet (LB-9
`upsert` does not populate it either; see decision above). Because this
uses `INNER JOIN`, snapshots whose legacy row is not yet linked quietly
drop from results (empty list, not an exception) — callers need not
prepare for exceptions assuming existence.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

import asyncpg

_SELECT_SQL = """
    SELECT p.id AS legacy_position_id, p.execution_id, p.strategy_id, p.symbol,
           p.exchange, ps.quantity, ps.avg_cost AS average_entry_price,
           ps.realized_pnl_base AS realized_pnl,
           COALESCE(ps.unrealized_pnl_base, 0) AS unrealized_pnl, p.closed_at
    FROM pos_snapshot ps
    JOIN positions p ON p.id = ps.legacy_position_id
    WHERE p.user_id = $1 AND p.symbol = $2 AND p.exchange = $3
    ORDER BY p.entry_time ASC
"""


@dataclass(frozen=True)
class LegacyPositionRow:
    """Projected result reconstructing one legacy `positions` row from
    `pos_snapshot`.

    Fields are the union of columns actually read by the three existing
    services — `risk_guard_service` / `portfolio_service` (quantity,
    realized_pnl, unrealized_pnl sum) and `report_service`
    (strategy_id, execution_id, realized_pnl, closed_at, closed positions
    only)."""

    legacy_position_id: int
    execution_id: int | None
    strategy_id: str
    symbol: str
    exchange: str
    quantity: Decimal
    average_entry_price: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    closed_at: datetime | None


class LegacyPositionsProjection:
    """Reads `pos_snapshot` in legacy `positions` query shape (read-only).

    Does not update any table — no `project`-style write methods exist on
    this class (see decision in module docstring above)."""

    async def get_positions(
        self,
        conn: asyncpg.Connection,
        *,
        user_id: UUID,
        symbol: str,
        exchange: str,
    ) -> list[LegacyPositionRow]:
        """All legacy counterpart positions for the same account/symbol
        (open and closed history, ordered by `entry_time` ascending —
        re-entries produce new rows). Returns empty list if no matching
        legacy row exists (not an exception)."""
        rows = await conn.fetch(_SELECT_SQL, user_id, symbol, exchange)
        return [
            LegacyPositionRow(
                legacy_position_id=row["legacy_position_id"],
                execution_id=row["execution_id"],
                strategy_id=row["strategy_id"],
                symbol=row["symbol"],
                exchange=row["exchange"],
                quantity=row["quantity"],
                average_entry_price=row["average_entry_price"],
                realized_pnl=row["realized_pnl"],
                unrealized_pnl=row["unrealized_pnl"],
                closed_at=row["closed_at"],
            )
            for row in rows
        ]
