"""9.4 / 9.4b — Circuit Breaker 4단계 상태전이 + 재가동 승인 워크플로.

Spec: 기능설계문서_v1.20.md#FD-9.4/FD-9.4b, 정책문서 8.6-B, ADR-2026-08-10-D

상태(normal/warning/restricted/halted/emergency)는 system_safety_state
테이블(04번, 단일 행)에 영속화 — 여러 FastAPI 워커가 공유해야 하므로
in-memory가 아니다.

핵심 원칙(정책문서 8.6-B) — warning/restricted는 조건 완화 시 자동 하향
허용하지만, **halted/emergency는 절대 자동 하향하지 않는다**. 조건이
완화되면 인간의 명시적 재가동 승인 요청(ApprovalService, PLATFORM scope,
180초 하한)을 생성하고, 승인 완료 시에만 정상 단계로 전이한다. 대기 중
조건이 재악화되면 요청을 자동 취소한다(악화된 상태로 재가동되는 경로
원천 차단).

여러 지표가 서로 다른 단계를 가리키면 가장 높은(위험한) 단계를 채택한다
(다수결/평균이 아니라 최댓값 — 안전장치는 항상 보수적으로).
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from decimal import Decimal
from enum import Enum
from typing import Any

import asyncpg
from pydantic import BaseModel

from src.core.approval import service as approval
from src.core.loader.risk_policy_loader import CircuitBreakerPolicy

PublishFn = Callable[[str, dict[str, Any]], Awaitable[None]]


class CircuitBreakerLevel(str, Enum):
    NORMAL = "normal"
    WARNING = "warning"
    RESTRICTED = "restricted"
    HALTED = "halted"
    EMERGENCY = "emergency"


_SEVERITY = {
    CircuitBreakerLevel.NORMAL: 0,
    CircuitBreakerLevel.WARNING: 1,
    CircuitBreakerLevel.RESTRICTED: 2,
    CircuitBreakerLevel.HALTED: 3,
    CircuitBreakerLevel.EMERGENCY: 4,
}

# 자동 하향이 허용되는 단계 — halted/emergency는 여기 없음(8.6-B 핵심 원칙).
_AUTO_DOWNGRADABLE = {CircuitBreakerLevel.WARNING, CircuitBreakerLevel.RESTRICTED}


class CircuitBreakerMetrics(BaseModel):
    api_error_rate_pct: Decimal = Decimal("0")
    data_delay_sec: Decimal = Decimal("0")
    order_reject_rate_pct: Decimal = Decimal("0")
    daily_loss_pct: Decimal = Decimal("0")
    api_disconnect_sec: Decimal = Decimal("0")


class CircuitBreakerState(BaseModel):
    level: CircuitBreakerLevel
    reactivation_approval_id: int | None


def compute_level(
    metrics: CircuitBreakerMetrics, policy: CircuitBreakerPolicy
) -> CircuitBreakerLevel:
    """§7.2 risk_policy.yaml의 circuit_breaker 임계치로 각 지표를 평가하고
    가장 높은 단계를 채택한다."""
    candidates = [CircuitBreakerLevel.NORMAL]

    if (
        metrics.daily_loss_pct >= policy.emergency.daily_loss_pct
        or metrics.api_disconnect_sec >= policy.emergency.api_disconnect_sec
    ):
        candidates.append(CircuitBreakerLevel.EMERGENCY)
    if metrics.data_delay_sec >= policy.halted.data_delay_sec:
        candidates.append(CircuitBreakerLevel.HALTED)
    if (
        metrics.api_error_rate_pct >= policy.restricted.api_error_rate_pct
        or metrics.order_reject_rate_pct >= policy.restricted.order_reject_rate_pct
    ):
        candidates.append(CircuitBreakerLevel.RESTRICTED)
    if (
        metrics.api_error_rate_pct >= policy.warning.api_error_rate_pct
        or metrics.data_delay_sec >= policy.warning.data_delay_sec
    ):
        candidates.append(CircuitBreakerLevel.WARNING)

    return max(candidates, key=lambda lvl: _SEVERITY[lvl])


class CircuitBreakerService:
    def __init__(
        self,
        pool: asyncpg.Pool,
        policy: CircuitBreakerPolicy,
        *,
        publish: PublishFn | None = None,
    ) -> None:
        self._pool = pool
        self._policy = policy
        self._publish = publish

    async def get_state(self) -> CircuitBreakerState:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT circuit_breaker_level, reactivation_approval_id "
                "FROM system_safety_state WHERE id = 1"
            )
        return CircuitBreakerState(
            level=CircuitBreakerLevel(row["circuit_breaker_level"]),
            reactivation_approval_id=row["reactivation_approval_id"],
        )

    async def evaluate(self, metrics: CircuitBreakerMetrics) -> CircuitBreakerState:
        computed = compute_level(metrics, self._policy)
        current = await self.get_state()
        computed_sev, current_sev = _SEVERITY[computed], _SEVERITY[current.level]

        if computed_sev >= current_sev:
            if current.reactivation_approval_id is not None:
                # 재가동 대기 중이던 상태가 재악화 — 요청 자동 취소(8.6-B 예외상황).
                await approval.cancel(self._pool, current.reactivation_approval_id)
                await self._set_level(current.level, reactivation_approval_id=None)
            if computed_sev > current_sev:
                await self._set_level(computed, reactivation_approval_id=None)
                await self._publish_level_changed(current.level, computed)
            return await self.get_state()

        # computed_sev < current_sev — 조건 완화
        if current.level in _AUTO_DOWNGRADABLE:
            await self._set_level(computed, reactivation_approval_id=None)
            await self._publish_level_changed(current.level, computed)
        elif current.reactivation_approval_id is None:
            request = await approval.create_request(
                self._pool,
                scope="PLATFORM",
                trigger_source="circuit_breaker_reactivation",
                requested_action="REACTIVATE_TO_NORMAL",
                context={"current_level": current.level.value, "computed_level": computed.value},
                approval_mode="SOLO",
            )
            await self._set_level(current.level, reactivation_approval_id=request.id)
            if self._publish is not None:
                await self._publish(
                    "risk.circuit_breaker.reactivation_requested",
                    {"approval_request_id": request.id, "current_level": current.level.value},
                )
        return await self.get_state()

    async def check_reactivation(self) -> CircuitBreakerState:
        """주기적으로 호출 — 승인이 완료된 재가동 요청을 실제 상태 전이로 반영한다."""
        current = await self.get_state()
        if current.reactivation_approval_id is None:
            return current

        request = await approval.get_request(self._pool, current.reactivation_approval_id)
        if request.status == "APPROVED":
            await self._set_level(CircuitBreakerLevel.NORMAL, reactivation_approval_id=None)
            await self._publish_level_changed(current.level, CircuitBreakerLevel.NORMAL)
        elif request.status in ("REJECTED", "EXPIRED", "CANCELLED"):
            await self._set_level(current.level, reactivation_approval_id=None)
        return await self.get_state()

    async def _set_level(
        self, level: CircuitBreakerLevel, *, reactivation_approval_id: int | None
    ) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE system_safety_state "
                "SET circuit_breaker_level = $1, reactivation_approval_id = $2, updated_at = now() "
                "WHERE id = 1",
                level.value,
                reactivation_approval_id,
            )

    async def _publish_level_changed(
        self, old_level: CircuitBreakerLevel, new_level: CircuitBreakerLevel
    ) -> None:
        if self._publish is not None:
            await self._publish(
                "risk.circuit_breaker.level_changed",
                {"old_level": old_level.value, "new_level": new_level.value},
            )
