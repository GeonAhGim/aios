"""FD-16.3/16.5 — 실행 시작/일시정지/손실한도/중지(start/pause/set_max_drawdown/retire).

Spec: 기능설계문서_v1.20.md#FD-16.3, 16.5, 06번 §6.1, 02번 §2.2. P6(파일당
300줄 상한) 준수를 위해 execution_service.py에서 "제어" 4개 동작만 이
모듈로 옮겼다(create_execution/convert_to_live는 "생성" 동작이라 그대로
남는다) — `ExecutionService`의 각 메서드는 여기 함수에 `self._pool` 등을
그대로 넘기는 얇은 위임일 뿐, 외부에서 보는 클래스 공개 계약
(`ExecutionService.start()` 등)은 전혀 바뀌지 않는다.

8.6-B Kill Switch 우선순위 원칙 — Watchdog/Circuit Breaker(FD-9)가 이미
PAUSED(paused_by=SAFETY_LAYER)로 전환한 실행은 사용자가 "시작"을 눌러도
거부된다(시스템 트리거가 사용자보다 우선). 사용자 자신이 일시정지
(paused_by=USER)한 것만 사용자가 재시작할 수 있다.
"""
from __future__ import annotations

from decimal import Decimal
from uuid import UUID

import asyncpg

from src.services.execution_types import ExecutionControlError, ExecutionSummary
from src.services.order_service.gate import GateOutcome, OrderContext, PreSubmitGate


async def start(
    pool: asyncpg.Pool,
    pre_start_gate: PreSubmitGate,
    execution_id: int,
    user_id: UUID,
) -> ExecutionSummary:
    async with pool.acquire() as conn:
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

        # 전수감사 §6 — DEPLOYMENT 게이트(48번 §3 게이트 1). RUNNING으로 실제
        # 전이하기 *전에* 검사해, 거부되면 UPDATE 자체를 안 하고 상태를 그대로
        # 둔다(order_service의 FSM-불변 원칙과 동일). EO-05 — 게이트가 필수
        # 인자가 됐으므로 None 분기 없이 항상 평가한다.
        decision = await pre_start_gate(
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
    pool: asyncpg.Pool,
    execution_id: int,
    *,
    paused_by: str = "USER",
    user_id: UUID | None = None,
) -> ExecutionSummary:
    if paused_by not in ("USER", "SAFETY_LAYER"):
        raise ExecutionControlError(f"알 수 없는 paused_by 값입니다: {paused_by}")

    async with pool.acquire() as conn:
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
            # #08 반영 — 방금 읽었을 때 RUNNING이었어도 그 사이 이미 다른
            # 경로(Watchdog 또는 동시 요청)가 상태를 바꿨을 수 있다. 조용히
            # 덮어쓰지 않고 충돌로 알린다.
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
    pool: asyncpg.Pool,
    execution_id: int,
    user_id: UUID,
    max_drawdown_pct: Decimal | None,
) -> ExecutionSummary:
    """ZuluTrade식 "위험 관리"(ZuluGuard) — 실행별 손실 한도(%)를 설정하면
    risk_guard_service.py::evaluate_all_running()이 주기적으로 실현+미실현
    손익을 이 한도와 비교해 초과 시 paused_by='SAFETY_LAYER'로 자동
    정지시킨다. None으로 설정하면 가드를 끈다(기본값)."""
    if max_drawdown_pct is not None and not (0 < max_drawdown_pct <= 100):
        raise ExecutionControlError("손실 한도는 0보다 크고 100 이하여야 합니다.")

    async with pool.acquire() as conn:
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
    pool: asyncpg.Pool,
    execution_id: int,
    user_id: UUID,
    *,
    liquidation: str = "KEEP_POSITIONS",
) -> ExecutionSummary:
    if liquidation not in ("IMMEDIATE_MARKET", "KEEP_POSITIONS"):
        raise ExecutionControlError(f"알 수 없는 청산 방식입니다: {liquidation}")

    async with pool.acquire() as conn:
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
            # #08과 같은 계열 — 동시에 이미 RETIRED/다른 상태로 전이된 경우
            # 조용히 성공을 가장하지 않는다.
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
