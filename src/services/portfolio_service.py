"""19.1 — 통합 포트폴리오 조회 (PortfolioService.get_portfolio).

Spec: 기능설계문서_v1.20.md#FD-19.1, FD-16.4, FD-3.2

사용자의 모든 RUNNING/PAUSED 실행(FD-16)을 하나의 포트폴리오 뷰로
집계한다 — 전략별 배분 비중, 미배분 현금 비중, 전체 손익. 02번 §2.2
Cross-Asset 원칙 — 이 합산은 순수 표시 목적(대시보드 집계)이며 FROZEN
Risk Engine의 실시간 판단에는 전혀 쓰이지 않는다.

범위 축소(Draft): 여러 거래소·통화에 걸친 실제 환산 합산에는 FX 변환이
필요한데 이 시스템에 아직 실제로 구동되는 환율 서비스가 없다 — Phase 1
실제 LIVE 대상이 crypto(Bitget) 단일 자산군뿐이라는 06번 §6.1/FD-16.2
전제를 그대로 따라, 총 현금 잔고(total_cash_balance)는 호출부가 이미
단일 통화로 정리해 전달한다고 가정한다(여러 거래소 잔고를 합산해서
넘기는 책임은 호출부).

weight_pct 합이 정확히 100%가 되도록 구성한다(FD-19.1 완료조건) —
total_portfolio_value = unallocated_cash + Σcurrent_value_i로 정의하면
Σweight_i + unallocated_cash_weight = 100%가 대수적으로 항상 성립한다.
"""
from __future__ import annotations

from decimal import Decimal
from uuid import UUID

import asyncpg
from pydantic import BaseModel

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


class PortfolioService:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

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
