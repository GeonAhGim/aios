"""19.1 — Integrated portfolio lookup (PortfolioService.get_portfolio).

Spec: 기능설계문서_v1.20.md#FD-19.1, FD-16.4, FD-3.2

Aggregates all RUNNING/PAUSED executions (FD-16) for a user into a single
portfolio view — strategy allocation weights, unallocated cash weight,
and total PnL. 02 §2.2 Cross-Asset principle — this sum is for display
only (dashboard aggregation) and is never used by the FROZEN Risk
Engine for real-time decisions.

Scope reduction (Draft): Actual FX-conversion aggregation across exchanges
and currencies would require an FX rate service, which this system does
not yet have in production. Following the assumption in 06 §6.1/FD-16.2
that Phase 1 LIVE targets are crypto (Bitget) only, we assume the caller
has already normalized total_cash_balance to a single currency
(the responsibility to sum exchange balances and pass them lies with the
caller).

weight_pct sums to exactly 100% (FD-19.1 done condition):
total_portfolio_value = unallocated_cash + Σcurrent_value_i, so
Σweight_i + unallocated_cash_weight = 100% algebraically always holds.

19.2 — Portfolio rebalance: increases in allocation are applied after
re-validating the limit (16.1); allocation increases for LIVE executions
always re-trigger approval (same principle as 16.2 — conservative "always
require approval" due to lack of automation-level tracking).
Decreasing allocation only lowers the limit; there is no code path that
forces liquidation of positions — the done condition "rebalance does not
liquidate existing positions" is naturally guaranteed by the fact that
this logic never touches the positions table at all. If the rebalanced
allocation total exceeds the balance, the entire write is rejected
(no partial updates, atomic).
"""
from __future__ import annotations

from decimal import Decimal
from uuid import UUID

import asyncpg
from pydantic import BaseModel

from src.core.approval import service as approval
from src.core.loader.risk_policy_loader import RiskPolicy
from src.services.approval_settings_service import ApprovalSettingsService
from src.services.capital_allocation import validate_capital_allocation

_HUNDRED = Decimal("100")


class PortfolioAllocation(BaseModel):
    execution_id: int
    strategy_id: str
    strategy_version: str
    exchange: str
    mode: str
    status: str
    allocated_capital: Decimal
    total_pnl: Decimal
    current_value: Decimal
    weight_pct: Decimal


class PortfolioView(BaseModel):
    allocations: list[PortfolioAllocation]
    unallocated_cash: Decimal
    unallocated_cash_weight_pct: Decimal
    total_portfolio_value: Decimal


class RebalanceAdjustment(BaseModel):
    execution_id: int
    new_allocated_capital: Decimal


class RebalanceError(Exception):
    """FD-19.2 failure — router converts to 400/403/404."""


class RebalanceResult(BaseModel):
    adjusted: int
    pending_approval: int
    approval_request_ids: list[int]


class PortfolioService:
    def __init__(self, pool: asyncpg.Pool, risk_policy: RiskPolicy) -> None:
        self._pool = pool
        self._risk_policy = risk_policy

    async def get_portfolio(
        self, user_id: UUID, *, total_cash_balance: Decimal
    ) -> PortfolioView:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT e.id AS execution_id, e.strategy_id, e.strategy_version, e.exchange,
                       e.mode, e.status, e.allocated_capital,
                       COALESCE(SUM(p.unrealized_pnl + p.realized_pnl), 0) AS total_pnl
                FROM strategy_executions e
                LEFT JOIN positions p ON p.execution_id = e.id
                WHERE e.user_id = $1 AND e.status IN ('RUNNING', 'PAUSED')
                GROUP BY e.id
                ORDER BY e.created_at ASC
                """,
                user_id,
            )

        allocated_total = sum((row["allocated_capital"] for row in rows), Decimal("0"))
        unallocated_cash = total_cash_balance - allocated_total

        current_values = [row["allocated_capital"] + row["total_pnl"] for row in rows]
        total_portfolio_value = unallocated_cash + sum(current_values, Decimal("0"))

        allocations = []
        for row, current_value in zip(rows, current_values, strict=True):
            weight_pct = (
                (current_value / total_portfolio_value * _HUNDRED)
                if total_portfolio_value != 0
                else Decimal("0")
            )
            allocations.append(
                PortfolioAllocation(
                    execution_id=row["execution_id"],
                    strategy_id=row["strategy_id"],
                    strategy_version=row["strategy_version"],
                    exchange=row["exchange"],
                    mode=row["mode"],
                    status=row["status"],
                    allocated_capital=row["allocated_capital"],
                    total_pnl=row["total_pnl"],
                    current_value=current_value,
                    weight_pct=weight_pct,
                )
            )

        unallocated_cash_weight_pct = (
            (unallocated_cash / total_portfolio_value * _HUNDRED)
            if total_portfolio_value != 0
            else _HUNDRED
        )

        return PortfolioView(
            allocations=allocations,
            unallocated_cash=unallocated_cash,
            unallocated_cash_weight_pct=unallocated_cash_weight_pct,
            total_portfolio_value=total_portfolio_value,
        )

    async def rebalance(
        self,
        user_id: UUID,
        adjustments: list[RebalanceAdjustment],
        *,
        total_cash_balance: Decimal,
    ) -> RebalanceResult:
        if not adjustments:
            raise RebalanceError("조정할 실행이 최소 1개 이상 필요합니다.")

        async with self._pool.acquire() as conn, conn.transaction():
            # Red team audit (docs/RED_TEAM_FINDINGS.md #09) — transaction +
            # FOR UPDATE locks all RUNNING/PAUSED executions for this user.
            # A second concurrent rebalance request will block on its own
            # SELECT ... FOR UPDATE until this transaction completes,
            # fundamentally preventing race conditions where both pass
            # without seeing each other's uncommitted changes.
            rows = await conn.fetch(
                "SELECT e.id AS execution_id, e.user_id, e.mode, e.allocated_capital, "
                "s.certified_badge "
                "FROM strategy_executions e "
                "JOIN strategies s ON s.strategy_id = e.strategy_id "
                "AND s.version = e.strategy_version "
                "WHERE e.id = ANY($1) AND e.status IN ('RUNNING', 'PAUSED') "
                "FOR UPDATE OF e",
                [a.execution_id for a in adjustments],
            )
            found = {row["execution_id"]: row for row in rows}

            for adjustment in adjustments:
                row = found.get(adjustment.execution_id)
                if row is None:
                    raise RebalanceError(
                        f"조정할 수 없는 실행입니다(존재하지 않거나 RUNNING/PAUSED가 "
                        f"아님): {adjustment.execution_id}"
                    )
                if row["user_id"] != user_id:
                    raise RebalanceError("본인의 실행만 재구성할 수 있습니다.")

            all_execution_ids = await conn.fetch(
                "SELECT id, allocated_capital FROM strategy_executions "
                "WHERE user_id = $1 AND status IN ('RUNNING', 'PAUSED') "
                "FOR UPDATE",
                user_id,
            )
            adjusted_by_id = {a.execution_id: a.new_allocated_capital for a in adjustments}
            new_total = sum(
                (
                    adjusted_by_id.get(row["id"], row["allocated_capital"])
                    for row in all_execution_ids
                ),
                Decimal("0"),
            )
            if new_total > total_cash_balance:
                raise RebalanceError(
                    f"재구성 결과 배분 총합({new_total})이 계좌 잔고"
                    f"({total_cash_balance})를 초과합니다."
                )

            for adjustment in adjustments:
                row = found[adjustment.execution_id]
                if adjustment.new_allocated_capital > row["allocated_capital"]:
                    validate_capital_allocation(
                        adjustment.new_allocated_capital,
                        total_cash_balance,
                        certified_badge=row["certified_badge"],
                        policy=self._risk_policy.strategy_allocation,
                    )

            approval_request_ids: list[int] = []
            pending_approval = 0
            for adjustment in adjustments:
                row = found[adjustment.execution_id]
                is_increase = adjustment.new_allocated_capital > row["allocated_capital"]

                await conn.execute(
                    "UPDATE strategy_executions SET allocated_capital = $2 WHERE id = $1",
                    adjustment.execution_id,
                    adjustment.new_allocated_capital,
                )

                if is_increase and row["mode"] == "LIVE":
                    settings = await ApprovalSettingsService(self._pool).get(user_id)
                    request = await approval.create_request(
                        self._pool,
                        scope="USER",
                        user_id=user_id,
                        trigger_source="execution_high_allocation",
                        requested_action="START_LIVE_EXECUTION",
                        context={
                            "execution_id": adjustment.execution_id,
                            "allocated_capital": adjustment.new_allocated_capital,
                            "rebalance": True,
                        },
                        approval_mode=settings.mode,
                    )
                    approval_request_ids.append(request.id)
                    pending_approval += 1

        return RebalanceResult(
            adjusted=len(adjustments),
            pending_approval=pending_approval,
            approval_request_ids=approval_request_ids,
        )
