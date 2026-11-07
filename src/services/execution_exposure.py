"""단계적 노출 (J8 LP-4): LIVE 전환 실행은 목표 자본의 일부로 시작하고, 확대는 별도 승인으로만 한다.

기존 장치를 그대로 쓴다 -- 코어(src/core/...)는 건드리지 않는다.

- allocated_capital: 지금 허용된 자본. 포트폴리오 엔진이 포지션 크기를 정할 때 쓰는 한도다.
- target_capital: 사용자가 승인한 최종 목표(마이그레이션 b7c1e5d93a40).
  단계 자본은 이를 넘지 못한다(DB 제약).
- max_loss_amount -> max_drawdown_pct: 사용자가 정한 손실 한도 금액을 지금 자본 대비 %로 환산해
  기존 실행별 손실 가드(risk_guard_service가 SAFETY_LAYER로 자동 일시정지)가 강제한다.
  자본이 늘면 %를 다시 환산한다.

자동 확대는 없다. expand는 사용자 요청(재인증은 라우터)으로만 불린다.
"""

from __future__ import annotations

from decimal import ROUND_DOWN, Decimal
from typing import Any
from uuid import UUID

import asyncpg

from src.core.logging.audit_log import record_audit_log
from src.services.capital_allocation import validate_capital_allocation
from src.services.execution_types import ExecutionControlError, ExecutionSummary

_HUNDRED = Decimal(100)
_CAPITAL_QUANT = Decimal("0.0000000001")  # NUMERIC(30,10)
_PCT_QUANT = Decimal("0.01")  # NUMERIC(5,2)
_MIN_PCT = Decimal("0.01")


def staged_capital(target_capital: Decimal, exposure_pct: Decimal) -> Decimal:
    """목표 자본의 exposure_pct%. 반올림은 내림 -- 승인한 것보다 많이 열리지 않는다."""
    return (target_capital * exposure_pct / _HUNDRED).quantize(_CAPITAL_QUANT, rounding=ROUND_DOWN)


def loss_guard_pct(max_loss_amount: Decimal, capital: Decimal) -> Decimal:
    """손실 한도 금액을 자본 대비 %로 환산한다(1~100 범위, 소수 2자리).

    내림으로 환산해 가드가 한도보다 먼저 걸리게 한다(더 엄격한 쪽).
    한도가 자본 이상이면 100%(전액)로 자른다.
    """
    if capital <= 0:
        raise ExecutionControlError("자본이 0 이하라 손실 가드를 환산할 수 없다.")
    pct = (max_loss_amount / capital * _HUNDRED).quantize(_PCT_QUANT, rounding=ROUND_DOWN)
    return min(_HUNDRED, max(_MIN_PCT, pct))


async def apply_initial_stage(
    conn: asyncpg.Connection,
    execution_id: int,
    *,
    target_capital: Decimal,
    initial_exposure_pct: Decimal,
    max_loss_amount: Decimal,
) -> tuple[Decimal, Decimal]:
    """방금 만든 LIVE 실행을 초기 노출 단계로 내린다. (단계 자본, 손실 가드 %)를 돌려준다."""
    capital = staged_capital(target_capital, initial_exposure_pct)
    if capital <= 0:
        raise ExecutionControlError("초기 노출이 너무 작아 허용 자본이 0이 된다.")
    guard = loss_guard_pct(max_loss_amount, capital)
    await conn.execute(
        "UPDATE strategy_executions SET target_capital = $2, allocated_capital = $3, "
        "max_loss_amount = $4, max_drawdown_pct = $5 WHERE id = $1",
        execution_id,
        target_capital,
        capital,
        max_loss_amount,
        guard,
    )
    return capital, guard


async def expand(
    pool: asyncpg.Pool,
    policy: Any,
    user_id: UUID,
    execution_id: int,
    *,
    new_exposure_pct: Decimal,
    available_balance: Decimal,
) -> ExecutionSummary:
    """LIVE 실행의 허용 자본을 목표의 new_exposure_pct%로 올린다. 줄이거나 같은 값은 거부한다."""
    async with pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
            "SELECT e.user_id, e.mode, e.status, e.exchange, e.allocated_capital, "
            "e.target_capital, e.max_loss_amount, e.max_drawdown_pct, s.certified_badge "
            "FROM strategy_executions e "
            "JOIN strategies s ON s.strategy_id = e.strategy_id AND s.version = e.strategy_version "
            "WHERE e.id = $1 FOR UPDATE OF e",
            execution_id,
        )
        if row is None:
            raise ExecutionControlError("존재하지 않는 실행입니다.")
        if row["user_id"] != user_id:
            raise ExecutionControlError("본인의 실행만 노출을 확대할 수 있습니다.")
        if row["mode"] != "LIVE":
            raise ExecutionControlError("LIVE 실행만 노출을 확대할 수 있습니다.")
        if row["status"] == "RETIRED":
            raise ExecutionControlError("종료된 실행은 노출을 확대할 수 없습니다.")
        if row["target_capital"] is None or row["max_loss_amount"] is None:
            raise ExecutionControlError(
                "단계적 노출로 전환된 실행이 아니다(목표 자본·손실 한도 없음)."
            )

        current: Decimal = row["allocated_capital"]
        target: Decimal = row["target_capital"]
        new_capital = staged_capital(target, new_exposure_pct)
        if new_capital <= current:
            raise ExecutionControlError(
                f"확대만 가능합니다 -- 요청 허용 자본({new_capital})이 현재({current}) 이하입니다."
            )
        if new_capital > target:
            raise ExecutionControlError("목표 자본을 넘길 수 없습니다.")
        validate_capital_allocation(
            new_capital,
            available_balance,
            certified_badge=row["certified_badge"],
            policy=policy.strategy_allocation,
        )
        guard_before = row["max_drawdown_pct"]
        guard_after = loss_guard_pct(row["max_loss_amount"], new_capital)
        await conn.execute(
            "UPDATE strategy_executions SET allocated_capital = $2, max_drawdown_pct = $3 "
            "WHERE id = $1",
            execution_id,
            new_capital,
            guard_after,
        )
        await record_audit_log(
            conn,
            actor_agent=str(user_id),
            action_type="execution.exposure_expanded",
            decision_data={
                "execution_id": execution_id,
                "allocated_capital_before": current,
                "allocated_capital_after": new_capital,
                "target_capital": target,
                "exposure_pct_after": new_exposure_pct,
                "max_loss_amount": row["max_loss_amount"],
                "max_drawdown_pct_before": guard_before,
                "max_drawdown_pct_after": guard_after,
            },
            user_id=user_id,
            target_type="strategy_execution",
            target_id=str(execution_id),
        )
    return ExecutionSummary(
        id=execution_id,
        status=row["status"],
        mode="LIVE",
        exchange=row["exchange"],
        allocated_capital=new_capital,
        max_drawdown_pct=guard_after,
    )
