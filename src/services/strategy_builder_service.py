"""14.3 — 전략 저장 및 생애주기 연동 (StrategyBuilderService).

Spec: 기능설계문서_v1.20.md#FD-14.3, 9.9(절대원칙), 13번 §13.5

FD-14.2(조건 조합 → FSM 컴파일)는 프론트엔드+컴파일러 영역이라 이 세션
(backend 전용) 스콥 밖 — 이 서비스는 이미 컴파일된 FSMStrategyConfig
JSON을 받아 저장하는 지점부터 시작한다.

9.9 절대원칙 — 생애주기는 반드시 정해진 순서(GENERATED→BACKTESTING→
VALIDATING→STRESS_TESTING→RISK_REVIEW→PAPER_TRADING→APPROVED→DEPLOYED→
MONITORING→REVIEW→RETIRED)를 예외 없이 통과해야 하며 건너뛸 수 없다.
REJECTED/FAILED는 이 순서 어느 단계에서든 진입 가능한 종단 상태다.

assert_executable()은 FD-14.3 예외상황("저장 직후 실행 시도 → 시스템
차단")의 실제 강제 지점 — FD-16(실행 제어판, 아직 없음)이
strategy_executions을 만들기 직전 호출해야 한다.
"""
from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import asyncpg
from pydantic import BaseModel

LIFECYCLE_ORDER = (
    "GENERATED",
    "BACKTESTING",
    "VALIDATING",
    "STRESS_TESTING",
    "RISK_REVIEW",
    "PAPER_TRADING",
    "APPROVED",
    "DEPLOYED",
    "MONITORING",
    "REVIEW",
    "RETIRED",
)
TERMINAL_FAILURE_STATUSES = ("REJECTED", "FAILED")
EXECUTABLE_STATUSES = frozenset({"APPROVED", "DEPLOYED", "MONITORING"})


class StrategyLifecycleError(Exception):
    """FD-14.3 실패 — 저장 거부 또는 잘못된 상태전이. 라우터가 400으로 변환."""


class SavedStrategy(BaseModel):
    strategy_id: str
    version: str
    lifecycle_status: str


class StrategyBuilderService:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def save_strategy(
        self,
        owner_user_id: UUID,
        strategy_id: str,
        version: str,
        *,
        target_asset: str,
        market: str,
        exchange: str,
        fsm_definition: dict[str, Any],
        author_agent: str = "user",
    ) -> SavedStrategy:
        async with self._pool.acquire() as conn:
            existing = await conn.fetchval(
                "SELECT 1 FROM strategies WHERE strategy_id = $1 AND version = $2",
                strategy_id,
                version,
            )
            if existing is not None:
                raise StrategyLifecycleError("이미 존재하는 strategy_id/version입니다.")

            await conn.execute(
                """
                INSERT INTO strategies
                    (strategy_id, version, owner_user_id, target_asset, market, exchange,
                     fsm_definition, author_agent, lifecycle_status)
                VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8, 'GENERATED')
                """,
                strategy_id,
                version,
                owner_user_id,
                target_asset,
                market,
                exchange,
                json.dumps(fsm_definition),
                author_agent,
            )
        return SavedStrategy(
            strategy_id=strategy_id, version=version, lifecycle_status="GENERATED"
        )

    async def transition_lifecycle(
        self, strategy_id: str, version: str, new_status: str
    ) -> SavedStrategy:
        if new_status not in LIFECYCLE_ORDER and new_status not in TERMINAL_FAILURE_STATUSES:
            raise StrategyLifecycleError(f"알 수 없는 생애주기 상태입니다: {new_status}")

        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT lifecycle_status FROM strategies WHERE strategy_id = $1 AND version = $2",
                strategy_id,
                version,
            )
            if row is None:
                raise StrategyLifecycleError("존재하지 않는 전략입니다.")
            current = row["lifecycle_status"]

            if new_status not in TERMINAL_FAILURE_STATUSES:
                if current not in LIFECYCLE_ORDER:
                    raise StrategyLifecycleError(
                        f"{current} 상태에서는 더 이상 전이할 수 없습니다."
                    )
                current_idx = LIFECYCLE_ORDER.index(current)
                new_idx = LIFECYCLE_ORDER.index(new_status)
                if new_idx != current_idx + 1:
                    raise StrategyLifecycleError(
                        f"생애주기를 건너뛸 수 없습니다: {current} 다음은 "
                        f"{LIFECYCLE_ORDER[current_idx + 1]}이어야 합니다(요청: {new_status})."
                    )

            await conn.execute(
                "UPDATE strategies SET lifecycle_status = $3, updated_at = now() "
                "WHERE strategy_id = $1 AND version = $2",
                strategy_id,
                version,
                new_status,
            )
        return SavedStrategy(strategy_id=strategy_id, version=version, lifecycle_status=new_status)


def assert_executable(lifecycle_status: str) -> None:
    if lifecycle_status not in EXECUTABLE_STATUSES:
        raise StrategyLifecycleError(
            f"현재 생애주기 단계({lifecycle_status})에서는 실행할 수 없습니다 — "
            f"{'/'.join(sorted(EXECUTABLE_STATUSES))} 상태여야 합니다."
        )
