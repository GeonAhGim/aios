"""16.2 — 실행 거래소·모드(PAPER/LIVE) 선택 (ExecutionService.create_execution).

Spec: 기능설계문서_v1.20.md#FD-16.2, 9.10, FD-10.1, FD-12, 06번 §6.1, 02번 §2.2

Zone 경계 — 이 서비스는 자본배분·거래소·모드를 지정해 strategy_executions
행을 만들 뿐이다. 실제로 "사고 팔지" 판단하는 로직은 여전히 FD-8
(FROZEN)의 배타적 책임이며 이 경계는 넘지 않는다.

mode=LIVE인 경우 자동화 수준(9.10)과 무관하게 항상 FD-10.1 Critical Risk
승인을 요구한다 — "9.10 자동화 수준이 Level 1~3인 경우"라는 조건부
트리거를 판정할 자동화 수준 추적 자체가 이 시스템에 아직 없다(별도
leaf 없음) — 안전 원칙상 더 보수적인 "항상 승인 필요"로 처리한다
(과소 안전장치보다 과잉 승인 요구가 안전한 방향).

승인 요청과 실행 행을 연결할 전용 컬럼이 strategy_executions에 없어
(설계 누락) approval_requests.context에 execution_id를 담아 연결한다 —
16.3(시작 제어)이 이 값으로 역참조해 승인 상태를 확인한다.

16.3 — 시작/일시정지/손실한도/중지(start/pause/set_max_drawdown/retire)는
P6(파일당 300줄 상한) 준수를 위해 `execution_control.py`로 옮겼다 — 이
클래스의 각 메서드는 거기 함수에 `self._pool` 등을 그대로 넘기는 얇은
위임이라 공개 계약(`ExecutionService.start()` 등)은 바뀌지 않는다.

16.6 — PAPER→LIVE 전환(convert_to_live): 기존 PAPER 실행은 종료하지
않고 그대로 이력 보존(성과 비교 근거), 신규 LIVE 실행을 별도 행으로
생성한다(converted_from_execution_id로 연결) — 가상 포지션이 실제
포지션으로 "마법처럼" 전환되는 경로 자체를 만들지 않는다(오해·오류
소지 원천 차단). create_execution()을 그대로 재사용해 16.1/16.2 절차
(자본배분·거래소·모드 검증, LIVE 승인)를 다시 거친다 — 승인 절차
생략 불가.
"""
from __future__ import annotations

from decimal import Decimal
from uuid import UUID

import asyncpg

from src.core.approval import service as approval
from src.core.loader.risk_policy_loader import RiskPolicy
from src.services import execution_control
from src.services.approval_settings_service import ApprovalSettingsService
from src.services.capital_allocation import validate_capital_allocation
from src.services.execution_types import (
    ExecutionControlError,
    ExecutionCreateError,
    ExecutionSummary,
)
from src.services.order_service.gate import PreSubmitGate
from src.services.strategy_builder_service import EXECUTABLE_STATUSES

__all__ = [
    "ExecutionControlError",
    "ExecutionCreateError",
    "ExecutionSummary",
    "ExecutionService",
]

KIS_EXCHANGE = "kis"
VALID_MODES = ("PAPER", "LIVE")


class ExecutionService:
    def __init__(
        self,
        pool: asyncpg.Pool,
        risk_policy: RiskPolicy,
        *,
        pre_start_gate: PreSubmitGate,
        publish: approval.PublishFn | None = None,
    ) -> None:
        self._pool = pool
        self._risk_policy = risk_policy
        self._publish = publish
        # 전수감사 §6 배선 — order_service.gate의 타입을 그대로 재사용한다
        # (이름은 "주문"이지만 모양(tenant/execution/exchange/mandate 하나
        # 평가해 ALLOW/DENY)이 완전히 같다 — order_service.foundation_gate.
        # make_foundation_pre_submit_gate()가 만든 콜러블을 여기 그대로
        # 주입해도 동작한다. 새 타입을 또 만들지 않는다). EO-05(I-01) —
        # 기본값을 없애 컴파일/타입체크 시점에 게이트 누락을 막는다.
        self._pre_start_gate = pre_start_gate

    async def create_execution(
        self,
        user_id: UUID,
        strategy_id: str,
        strategy_version: str,
        *,
        allocated_capital: Decimal,
        currency: str,
        exchange: str,
        mode: str,
        available_balance: Decimal,
    ) -> ExecutionSummary:
        if mode not in VALID_MODES:
            raise ExecutionCreateError(f"알 수 없는 실행 모드입니다: {mode}")
        if mode == "LIVE" and exchange == KIS_EXCHANGE:
            raise ExecutionCreateError("Phase 1은 암호화폐 거래소만 실거래 가능합니다.")

        async with self._pool.acquire() as conn:
            strategy = await conn.fetchrow(
                "SELECT lifecycle_status, certified_badge FROM strategies "
                "WHERE strategy_id = $1 AND version = $2",
                strategy_id,
                strategy_version,
            )
            if strategy is None:
                raise ExecutionCreateError("존재하지 않는 전략입니다.")
            if strategy["lifecycle_status"] not in EXECUTABLE_STATUSES:
                raise ExecutionCreateError(
                    "APPROVED 이상 상태에서만 실행 설정이 가능합니다"
                    f"(현재: {strategy['lifecycle_status']})."
                )

            credential = await conn.fetchval(
                "SELECT 1 FROM exchange_credentials "
                "WHERE user_id = $1 AND exchange = $2 AND is_active = true",
                user_id,
                exchange,
            )
            if credential is None:
                raise ExecutionCreateError(f"{exchange}에 연동된 자격증명이 없습니다.")

            validate_capital_allocation(
                allocated_capital,
                available_balance,
                certified_badge=strategy["certified_badge"],
                policy=self._risk_policy.strategy_allocation,
            )

            row = await conn.fetchrow(
                "INSERT INTO strategy_executions "
                "(strategy_id, strategy_version, user_id, exchange, mode, "
                " allocated_capital, currency) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING id, status",
                strategy_id,
                strategy_version,
                user_id,
                exchange,
                mode,
                allocated_capital,
                currency,
            )
            execution_id = row["id"]

        approval_request_id = None
        if mode == "LIVE":
            settings = await ApprovalSettingsService(self._pool).get(user_id)
            request = await approval.create_request(
                self._pool,
                scope="USER",
                user_id=user_id,
                trigger_source="execution_high_allocation",
                requested_action="START_LIVE_EXECUTION",
                context={
                    "execution_id": execution_id,
                    "allocated_capital": allocated_capital,
                },
                approval_mode=settings.mode,
                publish=self._publish,
            )
            approval_request_id = request.id

        return ExecutionSummary(
            id=execution_id,
            status=row["status"],
            mode=mode,
            exchange=exchange,
            allocated_capital=allocated_capital,
            approval_request_id=approval_request_id,
        )

    async def start(self, execution_id: int, user_id: UUID) -> ExecutionSummary:
        return await execution_control.start(
            self._pool, self._pre_start_gate, execution_id, user_id
        )

    async def pause(
        self,
        execution_id: int,
        *,
        paused_by: str = "USER",
        user_id: UUID | None = None,
    ) -> ExecutionSummary:
        return await execution_control.pause(
            self._pool, execution_id, paused_by=paused_by, user_id=user_id
        )

    async def set_max_drawdown(
        self, execution_id: int, user_id: UUID, max_drawdown_pct: Decimal | None
    ) -> ExecutionSummary:
        """ZuluTrade식 "위험 관리"(ZuluGuard) — 실행별 손실 한도(%)를 설정하면
        risk_guard_service.py::evaluate_all_running()이 주기적으로 실현+
        미실현 손익을 이 한도와 비교해 초과 시 paused_by='SAFETY_LAYER'로
        자동 정지시킨다. None으로 설정하면 가드를 끈다(기본값)."""
        return await execution_control.set_max_drawdown(
            self._pool, execution_id, user_id, max_drawdown_pct
        )

    async def retire(
        self, execution_id: int, user_id: UUID, *, liquidation: str = "KEEP_POSITIONS"
    ) -> ExecutionSummary:
        return await execution_control.retire(
            self._pool, execution_id, user_id, liquidation=liquidation
        )

    async def convert_to_live(
        self,
        user_id: UUID,
        source_execution_id: int,
        *,
        allocated_capital: Decimal,
        currency: str,
        exchange: str,
        available_balance: Decimal,
    ) -> ExecutionSummary:
        async with self._pool.acquire() as conn:
            source = await conn.fetchrow(
                "SELECT user_id, strategy_id, strategy_version, mode "
                "FROM strategy_executions WHERE id = $1",
                source_execution_id,
            )
            if source is None:
                raise ExecutionControlError("존재하지 않는 실행입니다.")
            if source["user_id"] != user_id:
                raise ExecutionControlError("본인의 실행만 전환할 수 있습니다.")
            if source["mode"] != "PAPER":
                raise ExecutionControlError("PAPER 모드 실행만 실매매로 전환할 수 있습니다.")

        result = await self.create_execution(
            user_id,
            source["strategy_id"],
            source["strategy_version"],
            allocated_capital=allocated_capital,
            currency=currency,
            exchange=exchange,
            mode="LIVE",
            available_balance=available_balance,
        )

        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE strategy_executions SET converted_from_execution_id = $2 WHERE id = $1",
                result.id,
                source_execution_id,
            )
        return result
