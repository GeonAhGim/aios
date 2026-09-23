"""20.1 — Period-based report aggregation API (ReportService).

Spec: design_doc_v1.20.md#FD-20.1, FD-3.2/3.3, 9.4

Design principle (Draft, policy doc 17.9-A avoid over-engineering) —
aggregate on the fly per request without persisting. When execution_id
is provided, aggregate only that execution; otherwise aggregate the
user's full portfolio.

Only positions with a closed_at value (closed/liquidated) are included
in realized PnL / win rate / trade count aggregation — we rely on the
existing convention (see positions migration comment) where positions
with quantity=0 are not deleted from rows but instead have closed_at
recorded to preserve them as history.

MDD is computed as the maximum drawdown (absolute amount, peak-to-trough)
of the daily cumulative PnL curve within this report period — expressed
as an absolute amount rather than a percentage because a starting capital
baseline is not available in this query scope (Draft).
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

import asyncpg
from pydantic import BaseModel


class DailyPnL(BaseModel):
    trade_date: date
    daily_pnl: Decimal
    cumulative_pnl: Decimal


class StrategyContribution(BaseModel):
    strategy_id: str
    strategy_version: str
    realized_pnl: Decimal
    trade_count: int


class ReportSummary(BaseModel):
    period_start: date
    period_end: date
    total_return: Decimal
    win_rate: Decimal | None
    max_drawdown: Decimal
    trade_count: int
    strategy_contributions: list[StrategyContribution]
    daily_pnl: list[DailyPnL]


class ReportService:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def generate_report(
        self,
        user_id: UUID,
        period_start: date,
        period_end: date,
        *,
        execution_id: int | None = None,
    ) -> ReportSummary:
        async with self._pool.acquire() as conn:
            condition = (
                "p.user_id = $1 AND p.closed_at IS NOT NULL "
                "AND (p.closed_at AT TIME ZONE 'UTC')::date BETWEEN $2 AND $3"
            )
            params: list[Any] = [user_id, period_start, period_end]
            if execution_id is not None:
                condition += " AND p.execution_id = $4"
                params.append(execution_id)

            rows = await conn.fetch(
                f"""
                SELECT p.strategy_id, p.execution_id, p.realized_pnl,
                       (p.closed_at AT TIME ZONE 'UTC')::date AS trade_date
                FROM positions p
                WHERE {condition}
                ORDER BY p.closed_at ASC
                """,
                *params,
            )

            exec_versions: dict[int, str] = {}
            exec_ids = {row["execution_id"] for row in rows if row["execution_id"] is not None}
            if exec_ids:
                version_rows = await conn.fetch(
                    "SELECT id, strategy_version FROM strategy_executions WHERE id = ANY($1)",
                    list(exec_ids),
                )
                exec_versions = {r["id"]: r["strategy_version"] for r in version_rows}

        if not rows:
            return ReportSummary(
                period_start=period_start,
                period_end=period_end,
                total_return=Decimal("0"),
                win_rate=None,
                max_drawdown=Decimal("0"),
                trade_count=0,
                strategy_contributions=[],
                daily_pnl=[],
            )

        total_return = sum((row["realized_pnl"] for row in rows), Decimal("0"))
        trade_count = len(rows)
        wins = sum(1 for row in rows if row["realized_pnl"] > 0)
        win_rate = Decimal(wins) / Decimal(trade_count) * Decimal("100")

        contributions: dict[tuple[str, str], dict[str, Any]] = {}
        for row in rows:
            version = exec_versions.get(row["execution_id"], "")
            key = (row["strategy_id"], version)
            entry = contributions.setdefault(
                key, {"realized_pnl": Decimal("0"), "trade_count": 0}
            )
            entry["realized_pnl"] += row["realized_pnl"]
            entry["trade_count"] += 1

        strategy_contributions = [
            StrategyContribution(strategy_id=sid, strategy_version=ver, **data)
            for (sid, ver), data in contributions.items()
        ]

        daily_totals: dict[date, Decimal] = {}
        for row in rows:
            daily_totals[row["trade_date"]] = (
                daily_totals.get(row["trade_date"], Decimal("0")) + row["realized_pnl"]
            )

        daily_pnl = []
        cumulative = Decimal("0")
        peak = Decimal("0")
        max_drawdown = Decimal("0")
        for trade_date in sorted(daily_totals):
            day_pnl = daily_totals[trade_date]
            cumulative += day_pnl
            peak = max(peak, cumulative)
            max_drawdown = max(max_drawdown, peak - cumulative)
            daily_pnl.append(
                DailyPnL(trade_date=trade_date, daily_pnl=day_pnl, cumulative_pnl=cumulative)
            )

        return ReportSummary(
            period_start=period_start,
            period_end=period_end,
            total_return=total_return,
            win_rate=win_rate,
            max_drawdown=max_drawdown,
            trade_count=trade_count,
            strategy_contributions=strategy_contributions,
            daily_pnl=daily_pnl,
        )
