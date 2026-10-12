"""16.4 — Execution strategy monitoring (ExecutionMonitoringService, read-only).

Spec: functional_spec_v1.20.md#FD-16.4, FD-3.2/3.3, 9.4(assessment_metrics)

Joins strategy_executions with positions (linked by execution_id,
FD-16.5/ADR-2026-08-10-C) to aggregate realized/unrealized PnL per
execution. Because the actual order-filling pipeline is not yet wired
(BitgetAdapter's order placement path falls under FD-4/FD-8, outside
this session's scope), executions without position rows will honestly
show 0 PnL — this is not a bug but a faithful reflection of the
current state.

When no strategy is running, returns an empty list rather than raising
an error (FD-16.4 exception — guidance messages are the frontend's
responsibility).
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

import asyncpg
from pydantic import BaseModel


class LastRiskVerdict(BaseModel):
    """Most recent risk gate verdict for this execution, from risk_decision
    (R-24 WORM ledger). risk_decision.execution_ref is already populated as
    'exec:' || orders.execution_id (format enforced by the
    a7c3d9e1f2b4_orders_risk_decision_intent_guard.py trigger), so this
    read-only join needs no new column or migration."""

    outcome: str
    reason_codes: list[str]
    evaluated_at: datetime


class ExecutionCard(BaseModel):
    execution_id: int
    strategy_id: str
    strategy_version: str
    status: str
    mode: str
    exchange: str
    allocated_capital: Decimal
    days_since_start: int | None
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    max_drawdown_pct: Decimal | None
    last_risk_verdict: LastRiskVerdict | None = None


class ExecutionMonitoringService:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def list_for_user(self, user_id: UUID) -> list[ExecutionCard]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT e.id AS execution_id, e.strategy_id, e.strategy_version, e.status,
                       e.mode, e.exchange, e.allocated_capital, e.started_at,
                       e.max_drawdown_pct,
                       COALESCE(SUM(p.realized_pnl), 0) AS realized_pnl,
                       COALESCE(SUM(p.unrealized_pnl), 0) AS unrealized_pnl,
                       rd.outcome AS risk_outcome,
                       rd.reason_codes AS risk_reason_codes,
                       rd.evaluated_at AS risk_evaluated_at
                FROM strategy_executions e
                LEFT JOIN positions p ON p.execution_id = e.id
                LEFT JOIN LATERAL (
                    SELECT outcome, reason_codes, evaluated_at
                    FROM risk_decision
                    WHERE execution_ref = 'exec:' || e.id::text
                    ORDER BY evaluated_at DESC
                    LIMIT 1
                ) rd ON true
                WHERE e.user_id = $1
                GROUP BY e.id, rd.outcome, rd.reason_codes, rd.evaluated_at
                ORDER BY e.created_at DESC
                """,
                user_id,
            )

        now = datetime.now(timezone.utc)
        cards = []
        for row in rows:
            started_at = row["started_at"]
            days_since_start = (now - started_at).days if started_at is not None else None
            risk_outcome = row["risk_outcome"]
            last_risk_verdict = (
                LastRiskVerdict(
                    outcome=risk_outcome,
                    reason_codes=list(row["risk_reason_codes"] or []),
                    evaluated_at=row["risk_evaluated_at"],
                )
                if risk_outcome is not None
                else None
            )
            cards.append(
                ExecutionCard(
                    execution_id=row["execution_id"],
                    strategy_id=row["strategy_id"],
                    strategy_version=row["strategy_version"],
                    status=row["status"],
                    mode=row["mode"],
                    exchange=row["exchange"],
                    allocated_capital=row["allocated_capital"],
                    days_since_start=days_since_start,
                    realized_pnl=row["realized_pnl"],
                    unrealized_pnl=row["unrealized_pnl"],
                    max_drawdown_pct=row["max_drawdown_pct"],
                    last_risk_verdict=last_risk_verdict,
                )
            )
        return cards
