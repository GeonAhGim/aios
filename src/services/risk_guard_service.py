"""FD-16 — 실행별 손실 한도 자동 정지(RiskGuardService, ZuluGuard식).

Spec: docs/specs/L4_risk_and_safety_v1.0.md §2 135행·§9 R-41 — pause() 대신
KillSwitchService.activate(STRATEGY_DEPLOYMENT, "exec:<id>")로 통일한다(DoD
"pause 직접 호출 0건", I3(§8) 유일한 진입점). 실제 정지(control 생성→
fence++→legacy 정지→fan-out)는 전부 그쪽에 위임하고(R-40 배선 비복제),
이미 ACTIVE인 control이 있는 실행은 재호출하지 않는다(멱등은 이 계층 책임).
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from decimal import Decimal
from typing import Any

import asyncpg

from src.core.observability.context import current
from src.foundation.risk_gate.domain.models import SafetyScope
from src.services.safety.kill_switch_service import KillSwitchService

PublishFn = Callable[[str, dict[str, Any]], Awaitable[None]]


class RiskGuardService:
    def __init__(
        self,
        pool: asyncpg.Pool,
        kill_switch: KillSwitchService,
        *,
        publish: PublishFn | None = None,
    ) -> None:
        self._pool = pool
        self._kill_switch = kill_switch
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
        triggered: list[int] = []
        for row in rows:
            allocated = row["allocated_capital"]
            if allocated is None or allocated <= 0:
                continue
            pnl = row["realized_pnl"] + row["unrealized_pnl"]
            drawdown_pct = (-pnl / allocated) * Decimal(100)
            if drawdown_pct < row["max_drawdown_pct"]:
                continue
            scope_ref = f"exec:{row['execution_id']}"
            async with self._pool.acquire() as conn:
                active = await conn.fetchval(
                    "SELECT 1 FROM safety_control WHERE scope = 'STRATEGY_DEPLOYMENT' "
                    "AND scope_ref = $1 AND state = 'ACTIVE'",
                    scope_ref,
                )
            if active is not None:
                continue
            view = await self._kill_switch.activate(
                scope=SafetyScope.STRATEGY_DEPLOYMENT,
                scope_ref=scope_ref,
                reason="MAX_DRAWDOWN_EXCEEDED",
                actor_subject_id=row["user_id"],
                actor_is_admin=True,
                trace_id=current().trace_id,
            )
            triggered.append(row["execution_id"])
            if self._publish is not None:
                await self._publish(
                    "execution.safety_block.applied",
                    {
                        "user_id": str(row["user_id"]),
                        "execution_id": row["execution_id"],
                        "reason": "MAX_DRAWDOWN_EXCEEDED",
                        "drawdown_pct": str(drawdown_pct),
                        "safety_control_id": str(view.id),
                    },
                )
        return triggered
