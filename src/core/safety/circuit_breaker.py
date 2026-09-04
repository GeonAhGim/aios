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

import logging
from collections.abc import Awaitable, Callable
from decimal import Decimal
from enum import Enum
from typing import Any

import asyncpg
from pydantic import BaseModel

from src.core.approval import service as approval
from src.core.db.conditional_write import ConcurrencyConflictError
from src.core.loader.risk_policy_loader import CircuitBreakerPolicy

logger = logging.getLogger(__name__)

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
    # None = 관측 0건("모름"), Decimal("0") = 관측됐고 지연 없음("앎"). 두 상태를
    # 섞지 않는다 — compute_level의 _exceeds_or_unknown()이 None을 항상 임계
    # 초과(fail-closed)로 취급한다. 기본값은 0(명시적으로 지연 없음)으로 남긴다 —
    # 이 필드를 신경 쓰지 않는 기존 호출부(다른 지표를 테스트하는 코드)가
    # 실측 배선 여부와 무관하게 이전과 같은 NORMAL 판정을 받도록 하기 위함이다.
    data_delay_sec: Decimal | None = Decimal("0")
    order_reject_rate_pct: Decimal = Decimal("0")
    daily_loss_pct: Decimal = Decimal("0")
    api_disconnect_sec: Decimal = Decimal("0")


class CircuitBreakerState(BaseModel):
    level: CircuitBreakerLevel
    reactivation_approval_id: int | None


def _exceeds_or_unknown(value: Decimal | None, threshold: float) -> bool:
    """`value`가 None이면 관측 0건("모름")이다 — 모름을 "임계 미만이라 안전"으로
    읽으면 fail-open이 된다(R3/R7 결함 원문). 미상은 항상 임계 초과로 취급한다."""
    if value is None:
        return True
    return value >= threshold


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
    if _exceeds_or_unknown(metrics.data_delay_sec, policy.halted.data_delay_sec):
        candidates.append(CircuitBreakerLevel.HALTED)
    if (
        metrics.api_error_rate_pct >= policy.restricted.api_error_rate_pct
        or metrics.order_reject_rate_pct >= policy.restricted.order_reject_rate_pct
    ):
        candidates.append(CircuitBreakerLevel.RESTRICTED)
    if metrics.api_error_rate_pct >= policy.warning.api_error_rate_pct or _exceeds_or_unknown(
        metrics.data_delay_sec, policy.warning.data_delay_sec
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
                cancelled_request_id = current.reactivation_approval_id
                await approval.cancel(self._pool, cancelled_request_id)
                await self._set_level(
                    current.level,
                    reactivation_approval_id=None,
                    expected=current,
                )
                current = CircuitBreakerState(level=current.level, reactivation_approval_id=None)
                if self._publish is not None:
                    await self._publish(
                        "risk.circuit_breaker.reactivation_cancelled",
                        {
                            "event_type": "risk.circuit_breaker.reactivation_cancelled",
                            "approval_request_id": cancelled_request_id,
                            "level": current.level.value,
                        },
                    )
            if computed_sev > current_sev:
                await self._set_level(computed, reactivation_approval_id=None, expected=current)
                await self._publish_level_changed(current.level, computed)
            return await self.get_state()

        # computed_sev < current_sev — 조건 완화
        if current.level in _AUTO_DOWNGRADABLE:
            await self._set_level(computed, reactivation_approval_id=None, expected=current)
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
            await self._set_level(
                current.level, reactivation_approval_id=request.id, expected=current
            )
            if self._publish is not None:
                await self._publish(
                    "risk.circuit_breaker.reactivation_requested",
                    {
                        "event_type": "risk.circuit_breaker.reactivation_requested",
                        "approval_request_id": request.id,
                        "current_level": current.level.value,
                    },
                )
        return await self.get_state()

    async def force_escalate(
        self, min_level: CircuitBreakerLevel, reason: str
    ) -> CircuitBreakerState:
        """9.6 Reconciliation 에스컬레이션 등 다른 안전장치가 직접 특정 단계
        이상으로 강제 격상할 때 사용 — 절대 하향은 하지 않는다(이미 그
        이상이면 그대로 유지, "더 격상하지 않는다" 원칙과 동일하게 여러 번
        호출해도 멱등적으로 동작)."""
        current = await self.get_state()
        if _SEVERITY[min_level] > _SEVERITY[current.level]:
            if current.reactivation_approval_id is not None:
                await approval.cancel(self._pool, current.reactivation_approval_id)
            await self._set_level(min_level, reactivation_approval_id=None, expected=current)
            await self._publish_level_changed(current.level, min_level)
            logger.critical(
                "Circuit Breaker 강제 격상: %s -> %s (%s)",
                current.level.value,
                min_level.value,
                reason,
            )
        return await self.get_state()

    async def check_reactivation(self) -> CircuitBreakerState:
        """주기적으로 호출 — 승인이 완료된 재가동 요청을 실제 상태 전이로 반영한다."""
        current = await self.get_state()
        if current.reactivation_approval_id is None:
            return current

        request = await approval.get_request(self._pool, current.reactivation_approval_id)
        if request.status == "APPROVED":
            await self._set_level(
                CircuitBreakerLevel.NORMAL, reactivation_approval_id=None, expected=current
            )
            await self._publish_level_changed(current.level, CircuitBreakerLevel.NORMAL)
            if self._publish is not None:
                await self._publish(
                    "risk.circuit_breaker.reactivated",
                    {
                        "event_type": "risk.circuit_breaker.reactivated",
                        "approval_request_id": request.id,
                        "old_level": current.level.value,
                    },
                )
        elif request.status in ("REJECTED", "EXPIRED", "CANCELLED"):
            await self._set_level(current.level, reactivation_approval_id=None, expected=current)
        return await self.get_state()

    async def _set_level(
        self,
        level: CircuitBreakerLevel,
        *,
        reactivation_approval_id: int | None,
        expected: CircuitBreakerState,
    ) -> None:
        """105번 §4.2 형태 B — `expected`(직전 `get_state()`가 읽은 값)를 WHERE
        조건으로 고정하는 CAS. 갱신 0행(다른 트랜잭션이 그 사이 먼저 썼음)이면
        조용히 성공한 것처럼 흘려보내지 않고 ConcurrencyConflictError를 던져
        호출자가 재조회 후 재판정하게 한다 — 무조건 UPDATE는 105 위반이다."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "UPDATE system_safety_state "
                "SET circuit_breaker_level = $1, reactivation_approval_id = $2, updated_at = now() "
                "WHERE id = 1 AND circuit_breaker_level = $3 "
                "AND reactivation_approval_id IS NOT DISTINCT FROM $4 "
                "RETURNING id",
                level.value,
                reactivation_approval_id,
                expected.level.value,
                expected.reactivation_approval_id,
            )
        if row is None:
            raise ConcurrencyConflictError(
                "system_safety_state: 다른 요청이 먼저 circuit breaker 상태를 "
                "바꿨습니다(동시 처리 충돌) — 다시 조회 후 재판정하세요."
            )

    async def _publish_level_changed(
        self, old_level: CircuitBreakerLevel, new_level: CircuitBreakerLevel
    ) -> None:
        if self._publish is not None:
            await self._publish(
                "risk.circuit_breaker.level_changed",
                {"old_level": old_level.value, "new_level": new_level.value},
            )
