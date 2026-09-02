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

16.3 — 시작/일시정지/중지(start/pause/retire): 8.6-B Kill Switch 우선순위
원칙 — Watchdog/Circuit Breaker(FD-9)가 이미 PAUSED(paused_by=
SAFETY_LAYER)로 전환한 실행은 사용자가 "시작"을 눌러도 거부된다(시스템
트리거가 사용자보다 우선). 사용자 자신이 일시정지(paused_by=USER)한
것만 사용자가 재시작할 수 있다.

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
from pydantic import BaseModel

from src.core.approval import service as approval
from src.core.loader.risk_policy_loader import RiskPolicy
from src.services.approval_settings_service import ApprovalSettingsService
from src.services.capital_allocation import validate_capital_allocation
from src.services.order_service.gate import GateOutcome, OrderContext, PreSubmitGate
from src.services.strategy_builder_service import EXECUTABLE_STATUSES

KIS_EXCHANGE = "kis"
VALID_MODES = ("PAPER", "LIVE")


class ExecutionCreateError(Exception):
    """FD-16.1/16.2 실패 — 라우터가 400/403/404로 변환."""


class ExecutionControlError(Exception):
    """FD-16.3 실패 — 시작/일시정지/중지 거부. 라우터가 400/403/404로 변환."""


class ExecutionSummary(BaseModel):
    id: int
    status: str
    mode: str
    exchange: str
    allocated_capital: Decimal
    approval_request_id: int | None = None
    max_drawdown_pct: Decimal | None = None


class ExecutionService:
    def __init__(
        self,
        pool: asyncpg.Pool,
        risk_policy: RiskPolicy,
        *,
        publish: approval.PublishFn | None = None,
        pre_start_gate: PreSubmitGate | None = None,
    ) -> None:
        self._pool = pool
        self._risk_policy = risk_policy
        self._publish = publish
        # 전수감사 §6 배선 — order_service.gate의 타입을 그대로 재사용한다
        # (이름은 "주문"이지만 모양(tenant/execution/exchange/mandate 하나
        # 평가해 ALLOW/DENY)이 완전히 같다 — order_service.foundation_gate.
        # make_foundation_pre_submit_gate()가 만든 콜러블을 여기 그대로
        # 주입해도 동작한다. 새 타입을 또 만들지 않는다).
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
        async with self._pool.acquire() as conn:
            execution = await conn.fetchrow(
                "SELECT user_id, status, mode, paused_by, allocated_capital, exchange "
                "FROM strategy_executions WHERE id = $1",
                execution_id,
            )
            if execution is None:
                raise ExecutionControlError("존재하지 않는 실행입니다.")
            if execution["user_id"] != user_id:
                raise ExecutionControlError("본인의 실행만 제어할 수 있습니다.")
            if execution["status"] == "RETIRED":
                raise ExecutionControlError("이미 중지된 실행은 다시 시작할 수 없습니다.")

            if execution["status"] == "PAUSED" and execution["paused_by"] == "SAFETY_LAYER":
                raise ExecutionControlError(
                    "안전장치(Watchdog/Circuit Breaker)가 정지시킨 실행입니다 — "
                    "사용자가 직접 재시작할 수 없습니다."
                )

            if execution["status"] == "PENDING_APPROVAL" and execution["mode"] == "LIVE":
                approved = await conn.fetchval(
                    "SELECT status FROM approval_requests "
                    "WHERE trigger_source = 'execution_high_allocation' "
                    "AND (context->>'execution_id')::bigint = $1 "
                    "ORDER BY created_at DESC LIMIT 1",
                    execution_id,
                )
                if approved != "APPROVED":
                    raise ExecutionControlError(
                        f"LIVE 실행은 승인이 완료되어야 시작할 수 있습니다(현재 승인 상태: "
                        f"{approved or '요청 없음'})."
                    )

            # 전수감사 §6 — DEPLOYMENT 게이트(48번 §3 게이트 1). RUNNING으로
            # 실제 전이하기 *전에* 검사해, 거부되면 UPDATE 자체를 안 하고
            # 상태를 그대로 둔다(order_service의 FSM-불변 원칙과 동일).
            if self._pre_start_gate is not None:
                decision = await self._pre_start_gate(
                    OrderContext(
                        user_id=user_id,
                        execution_id=execution_id,
                        exchange=execution["exchange"],
                        mandate_revision_id=None,  # 컬럼 없음 — 마이그레이션 대기
                    )
                )
                if decision.outcome != GateOutcome.ALLOW:
                    raise ExecutionControlError(
                        f"위험 게이트가 시작을 거부했습니다: {decision.reason_codes}"
                    )

            row = await conn.fetchrow(
                "UPDATE strategy_executions "
                "SET status = 'RUNNING', paused_by = NULL, "
                "started_at = COALESCE(started_at, now()) "
                "WHERE id = $1 AND status = $2 "
                "RETURNING status, mode, exchange, allocated_capital",
                execution_id,
                execution["status"],
            )
            if row is None:
                # 레드팀 감사(docs/RED_TEAM_FINDINGS.md #08) 반영 — 방금 읽은
                # status 그대로 조건을 걸어 그 사이 Watchdog(별도 프로세스)이
                # 안전정지를 먼저 커밋했다면 이 UPDATE 자체가 0행이 된다.
                # 8.6-B Kill Switch 우선순위 — 사용자 요청이 안전정지를
                # 조용히 덮어쓰지 않는다.
                raise ExecutionControlError(
                    "다른 프로세스가 이 실행의 상태를 방금 변경했습니다 — "
                    "다시 조회 후 시도하세요(안전장치가 정지시켰을 수 있습니다)."
                )
        return ExecutionSummary(
            id=execution_id,
            status=row["status"],
            mode=row["mode"],
            exchange=row["exchange"],
            allocated_capital=row["allocated_capital"],
        )

    async def pause(
        self,
        execution_id: int,
        *,
        paused_by: str = "USER",
        user_id: UUID | None = None,
    ) -> ExecutionSummary:
        if paused_by not in ("USER", "SAFETY_LAYER"):
            raise ExecutionControlError(f"알 수 없는 paused_by 값입니다: {paused_by}")

        async with self._pool.acquire() as conn:
            execution = await conn.fetchrow(
                "SELECT user_id, status, mode, exchange, allocated_capital "
                "FROM strategy_executions WHERE id = $1",
                execution_id,
            )
            if execution is None:
                raise ExecutionControlError("존재하지 않는 실행입니다.")
            if paused_by == "USER":
                if user_id is None or execution["user_id"] != user_id:
                    raise ExecutionControlError("본인의 실행만 제어할 수 있습니다.")
            if execution["status"] != "RUNNING":
                raise ExecutionControlError(
                    f"RUNNING 상태에서만 일시정지할 수 있습니다(현재: {execution['status']})."
                )

            row = await conn.fetchrow(
                "UPDATE strategy_executions SET status = 'PAUSED', paused_by = $2 "
                "WHERE id = $1 AND status = 'RUNNING' "
                "RETURNING status, mode, exchange, allocated_capital",
                execution_id,
                paused_by,
            )
            if row is None:
                # #08 반영 — 방금 읽었을 때 RUNNING이었어도 그 사이 이미
                # 다른 경로(Watchdog 또는 동시 요청)가 상태를 바꿨을 수
                # 있다. 조용히 덮어쓰지 않고 충돌로 알린다.
                raise ExecutionControlError(
                    "다른 프로세스가 이 실행을 방금 이미 정지시켰습니다 — 다시 조회하세요."
                )
        return ExecutionSummary(
            id=execution_id,
            status=row["status"],
            mode=row["mode"],
            exchange=row["exchange"],
            allocated_capital=row["allocated_capital"],
        )

    async def set_max_drawdown(
        self, execution_id: int, user_id: UUID, max_drawdown_pct: Decimal | None
    ) -> ExecutionSummary:
        """ZuluTrade식 "위험 관리"(ZuluGuard) — 실행별 손실 한도(%)를 설정하면
        risk_guard_service.py::evaluate_all_running()이 주기적으로 실현+
        미실현 손익을 이 한도와 비교해 초과 시 paused_by='SAFETY_LAYER'로
        자동 정지시킨다. None으로 설정하면 가드를 끈다(기본값)."""
        if max_drawdown_pct is not None and not (0 < max_drawdown_pct <= 100):
            raise ExecutionControlError("손실 한도는 0보다 크고 100 이하여야 합니다.")

        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "UPDATE strategy_executions SET max_drawdown_pct = $3 "
                "WHERE id = $1 AND user_id = $2 "
                "RETURNING status, mode, exchange, allocated_capital, max_drawdown_pct",
                execution_id,
                user_id,
                max_drawdown_pct,
            )
        if row is None:
            raise ExecutionControlError("본인의 실행만 제어할 수 있습니다.")
        return ExecutionSummary(
            id=execution_id,
            status=row["status"],
            mode=row["mode"],
            exchange=row["exchange"],
            allocated_capital=row["allocated_capital"],
            max_drawdown_pct=row["max_drawdown_pct"],
        )

    async def retire(
        self, execution_id: int, user_id: UUID, *, liquidation: str = "KEEP_POSITIONS"
    ) -> ExecutionSummary:
        if liquidation not in ("IMMEDIATE_MARKET", "KEEP_POSITIONS"):
            raise ExecutionControlError(f"알 수 없는 청산 방식입니다: {liquidation}")

        async with self._pool.acquire() as conn:
            execution = await conn.fetchrow(
                "SELECT user_id, status, mode, exchange, allocated_capital "
                "FROM strategy_executions WHERE id = $1",
                execution_id,
            )
            if execution is None:
                raise ExecutionControlError("존재하지 않는 실행입니다.")
            if execution["user_id"] != user_id:
                raise ExecutionControlError("본인의 실행만 제어할 수 있습니다.")
            if execution["status"] not in ("RUNNING", "PAUSED"):
                raise ExecutionControlError(
                    f"RUNNING/PAUSED 상태에서만 중지할 수 있습니다(현재: {execution['status']})."
                )

            row = await conn.fetchrow(
                "UPDATE strategy_executions "
                "SET status = 'RETIRED', retire_liquidation = $2, retired_at = now() "
                "WHERE id = $1 AND status IN ('RUNNING', 'PAUSED') "
                "RETURNING status, mode, exchange, allocated_capital",
                execution_id,
                liquidation,
            )
            if row is None:
                # #08과 같은 계열 — 동시에 이미 RETIRED/다른 상태로 전이된
                # 경우 조용히 성공을 가장하지 않는다.
                raise ExecutionControlError(
                    "다른 프로세스가 이 실행의 상태를 방금 이미 변경했습니다 — 다시 조회하세요."
                )
        return ExecutionSummary(
            id=execution_id,
            status=row["status"],
            mode=row["mode"],
            exchange=row["exchange"],
            allocated_capital=row["allocated_capital"],
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
