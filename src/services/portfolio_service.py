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

19.2 — 포트폴리오 재구성(rebalance): 배분 증가는 한도(16.1) 재검증 후
반영, LIVE 실행의 배분 증가는 항상 승인을 재트리거한다(16.2와 동일
원칙 — 자동화 수준 추적이 없어 "항상 승인 필요"로 보수적으로 처리).
배분 감소는 한도만 낮출 뿐 포지션을 강제 청산하는 코드 경로 자체가
없다 — "재구성이 기존 포지션을 청산하지 않는다"는 완료조건은 이
로직이 positions 테이블을 아예 건드리지 않는다는 사실로 자연히
보장된다. 재구성 결과 배분 총합이 잔고를 초과하면 전체를 저장 거부
(부분 반영 없음, 원자적).
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
    """FD-19.2 실패 — 라우터가 400/403/404로 변환."""


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
            # 레드팀 감사(docs/RED_TEAM_FINDINGS.md #09) 반영 — 트랜잭션 +
            # FOR UPDATE로 이 사용자의 RUNNING/PAUSED 실행 전체를 잠근다.
            # 동시에 들어온 두 번째 재구성 요청은 이 트랜잭션이 끝날 때까지
            # 자신의 SELECT ... FOR UPDATE에서 블록되므로, "서로의 아직
            # 커밋 안 된 변경을 못 본 채 각자 통과"하는 경합이 원천 차단된다.
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
