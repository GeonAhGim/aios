"""FD-16(신설) — 실행별 손실 한도 자동 정지 (RiskGuardService, ZuluGuard식).

Spec: 사용자 요청(2026-09-01) — ZuluTrade의 위험 관리(손실 한도 도달 시
자동 정지) 기능. execution_service.py::pause()가 이미 paused_by=
'SAFETY_LAYER'를 1급 값으로 지원하도록 설계돼 있었다(사람이 아닌 안전
장치가 실행을 멈추는 경로가 이미 있었음) — 이 서비스가 그 안전장치의
실제 판정 로직이다.

evaluate_all_running()은 main.py의 다른 백그라운드 루프(heartbeat_loop,
alert_service.py::evaluate_all_active)와 동일한 패턴으로 주기 실행된다.
손실률 = -(실현손익+미실현손익)/배분자본 × 100 — 양수면 손실. 배분자본이
0 이하인 실행(설계상 있을 수 없지만 방어적으로)은 0으로 나누지 않도록
건너뛴다. 이미 다른 경로가 상태를 바꿔 pause()가 실패해도(동시성 경쟁)
그 실행만 건너뛰고 다음 주기에 재평가한다.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from decimal import Decimal
from typing import Any

import asyncpg

from src.services.execution_service import ExecutionControlError, ExecutionService

PublishFn = Callable[[str, dict[str, Any]], Awaitable[None]]


class RiskGuardService:
    def __init__(
        self,
        pool: asyncpg.Pool,
        execution_service: ExecutionService,
        *,
        publish: PublishFn | None = None,
    ) -> None:
        self._pool = pool
        self._executions = execution_service
        self._publish = publish

    async def evaluate_all_running(self) -> list[int]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT e.id AS execution_id, e.user_id, e.allocated_capital, e.max_drawdown_pct,
                       COALESCE(SUM(p.realized_pnl), 0) AS realized_pnl,
                       COALESCE(SUM(p.unrealized_pnl), 0) AS unrealized_pnl
                FROM strategy_executions e
                LEFT JOIN positions p ON p.execution_id = e.id
                WHERE e.status = 'RUNNING' AND e.max_drawdown_pct IS NOT NULL
                GROUP BY e.id
                """
            )

        paused: list[int] = []
        for row in rows:
            allocated = row["allocated_capital"]
            if allocated is None or allocated <= 0:
                continue
            pnl = row["realized_pnl"] + row["unrealized_pnl"]
            drawdown_pct = (-pnl / allocated) * Decimal(100)
            if drawdown_pct < row["max_drawdown_pct"]:
                continue

            try:
                await self._executions.pause(row["execution_id"], paused_by="SAFETY_LAYER")
            except ExecutionControlError:
                continue

            paused.append(row["execution_id"])
            if self._publish is not None:
                await self._publish(
                    "execution.safety_block.applied",
                    {
                        "event_type": "execution.safety_block.applied",
                        "user_id": str(row["user_id"]),
                        "execution_id": row["execution_id"],
                        "reason": "MAX_DRAWDOWN_EXCEEDED",
                        "drawdown_pct": str(drawdown_pct),
                        "max_drawdown_pct": str(row["max_drawdown_pct"]),
                    },
                )

        return paused
