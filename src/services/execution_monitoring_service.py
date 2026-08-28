"""16.4 — 실행 중 전략 모니터링 (ExecutionMonitoringService, 조회 전용).

Spec: 기능설계문서_v1.20.md#FD-16.4, FD-3.2/3.3, 9.4(평가지표)

strategy_executions와 positions(execution_id로 연결, FD-16.5/ADR-2026-08-10-C)
를 조인해 실행별 실현/미실현 손익을 집계한다. 아직 실제 주문 체결
파이프라인이 배선되지 않아(BitgetAdapter가 실제 주문을 넣는 경로는
FD-4/FD-8 소관, 이 세션 스콥 밖) positions에 행이 없는 실행은 손익
0으로 정직하게 나타난다 — 이는 버그가 아니라 현재 상태를 그대로
반영한 것이다.

실행중인 전략이 하나도 없는 경우는 오류가 아니라 빈 목록을 반환한다
(FD-16.4 예외상황 — 안내 문구는 프론트엔드 몫).
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

import asyncpg
from pydantic import BaseModel


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


class ExecutionMonitoringService:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def list_for_user(self, user_id: UUID) -> list[ExecutionCard]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT e.id AS execution_id, e.strategy_id, e.strategy_version, e.status,
                       e.mode, e.exchange, e.allocated_capital, e.started_at,
                       COALESCE(SUM(p.realized_pnl), 0) AS realized_pnl,
                       COALESCE(SUM(p.unrealized_pnl), 0) AS unrealized_pnl
                FROM strategy_executions e
                LEFT JOIN positions p ON p.execution_id = e.id
                WHERE e.user_id = $1
                GROUP BY e.id
                ORDER BY e.created_at DESC
                """,
                user_id,
            )

        now = datetime.now(timezone.utc)
        cards = []
        for row in rows:
            started_at = row["started_at"]
            days_since_start = (now - started_at).days if started_at is not None else None
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
                )
            )
        return cards
