"""RECOVERY 게이트 조립 — L4_risk_and_safety_v1.0.md#§9 R-53, §2 표 110행,
§4.3 CB 표, I5(§8 394행). 선행: R-44 `src/core/safety/recovery_gate.py::
can_reactivate`(순수 판정, 4가지 거부 규칙은 여기서 다시 쓰지 않는다),
R-35 `evaluate_pre_submit.py`(조립 패턴 참고).

이 리프는 조립·영속·근거 기록만 한다:
1. `control_id`로 안전 통제(kill switch)를 조회한다. `SafetyControl`에는
   circuit breaker 세부 단계가 없다 — ACTIVE면 `HALTED`로, INACTIVE면
   `NORMAL`로 취급한다(재가동 대상 여부만 필요, R-44 계약이 요구하는
   `CircuitBreakerLevel`을 만족시키는 최소 매핑).
2. `approval_id`로 승인 요청을 조회한다. TTL 초과분은 R-45
   `circuit_breaker_loop._effective_status`와 동일 규칙으로 "APPROVED
   아님"으로 낮춘다(시계는 여기서만 본다 — can_reactivate는 보지 않는다).
3. cooldown — `metrics_history`(초당 1샘플) 영속 테이블이 아직 없다(§10
   미확정, R-54 이전이라 이 리프 범위 밖). 대신 `safety_control.created_at`
   ("마지막 트립")부터 경과한 초를 baseline(0) 샘플로 채운다 — "그 구간이
   실측으로 안전했다"는 증거가 아니라 "충분한 시간이 지났는가"만 대변한다
   ("미검증": 과거 구간의 실측 이력 자체는 아직 아무 데도 없다). 실측 조건은
   4번이 담당한다.
4. fresh 재평가 — `CircuitBreakerService.get_state()`로 지금 이 순간의 전역
   circuit breaker level을 다시 읽는다. R-45 tick 루프가 실측 지표로 계속
   갱신하는 값을 그대로 재사용할 뿐, 이 함수 자신은 지표를 새로 수집하지
   않는다.
5. 위 네 입력으로 `can_reactivate()`를 호출하고, 결과를 `RiskDecision`으로
   포장해 `RiskDecisionRecorder`(R-25)로 WORM 기록한다(ALLOW/DENY 모두).
6. ALLOW일 때만 기존 `deactivate_safety_control`(R-23 해제 커맨드)을 호출한다
   — 새 해제 경로를 만들지 않는다. DENY는 `RecoveryDeniedError`를 던져
   라우터가 EXCEPTION_MAP으로 403을 번역하게 한다(`RiskGateDeniedError`와
   동일한 관례, start_deployment.py 참조) — raw HTTPException 금지.
"""
from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import UUID, uuid5

from pydantic import BaseModel

from src.core.approval.service import ApprovalRequest
from src.core.risk.decision import GateKind, RiskDecision, RiskOutcome, RuleResult
from src.core.risk.hashing import canonical_json, sha256_hex
from src.core.safety.circuit_breaker import (
    CircuitBreakerLevel,
    CircuitBreakerMetrics,
    CircuitBreakerService,
)
from src.core.safety.recovery_gate import can_reactivate
from src.foundation.risk_gate.application.deactivate_safety_control import (
    SafetyControlNotFoundError,
    deactivate_safety_control,
)
from src.foundation.risk_gate.domain.models import SafetyControlState
from src.foundation.risk_gate.ports.repository import RiskGateRepository
from src.services.risk_decision_recorder import RiskDecisionRecorder

GetApprovalRequest = Callable[[int], Awaitable[ApprovalRequest]]

_RULE_VERSION = "risk_gate.recovery/1"
_ENGINE_VERSION = "risk_gate.recovery/1"
_RULE_HASH = sha256_hex(canonical_json({"gate_kind": "RECOVERY", "rule_version": _RULE_VERSION}))
_DECISION_ID_NAMESPACE = UUID("b3f1c2d4-6a7e-4c8b-9a1d-2e3f4a5b6c7d")
_TTL_SECONDS = 5.0
# spec §9 R-53 DoD — evidence 없음 거부는 taxonomy RSK-007로 노출한다. 다른
# 거부 사유는 can_reactivate의 원 코드를 그대로 쓴다(taxonomy 미확정).
_TAXONOMY = {"RECOVERY_EVIDENCE_MISSING": "RSK-007"}
_REACTIVATABLE_CB_LEVELS = (CircuitBreakerLevel.HALTED, CircuitBreakerLevel.EMERGENCY)


class RecoveryDeniedError(Exception):
    """DENY로 끝난 RiskDecision — 라우터가 EXCEPTION_MAP으로 403을 번역한다."""

    def __init__(self, decision: RiskDecision) -> None:
        super().__init__(f"recovery denied: {decision.reason_codes}")
        self.details = {"reason_codes": list(decision.reason_codes)}


@dataclass(frozen=True)
class RecoveryGateRepos:
    risk_gate: RiskGateRepository
    circuit_breaker: CircuitBreakerService
    get_approval_request: GetApprovalRequest
    decision_recorder: RiskDecisionRecorder
    cooldown_sec: int
    approval_ttl_sec: int


class _RecoveryInputs(BaseModel, frozen=True):
    schema_version: Literal["v1"] = "v1"
    tenant_id: UUID
    control_id: UUID
    control_scope: str
    control_reason: str
    approval_id: int
    approval_status: str
    evidence_ref: str | None
    current_level: str
    cb_level: str
    cooldown_sec: int
    elapsed_since_trip_sec: int
    as_of: datetime


def _effective_approval_status(
    request: ApprovalRequest, *, ttl_sec: int, now: datetime
) -> str:
    """R-45 `circuit_breaker_loop._effective_status`와 동일 규칙."""
    if request.status == "APPROVED" and request.resolved_at is not None:
        if now - request.resolved_at > timedelta(seconds=ttl_sec):
            return "EXPIRED"
    return request.status


def _cooldown_history(*, elapsed_sec: int, cooldown_sec: int) -> tuple[CircuitBreakerMetrics, ...]:
    n = min(max(elapsed_sec, 0), cooldown_sec)
    return tuple(CircuitBreakerMetrics() for _ in range(n))


async def evaluate_recovery(
    repos: RecoveryGateRepos,
    *,
    tenant_id: UUID,
    control_id: UUID,
    evidence_ref: str | None,
    approval_id: int,
    trace_id: UUID,
) -> RiskDecision:
    start_ns = time.perf_counter_ns()
    control = await repos.risk_gate.get_safety_control(control_id)
    if control is None:
        raise SafetyControlNotFoundError(str(control_id))

    now = datetime.now(timezone.utc)
    request = await repos.get_approval_request(approval_id)
    approval_status = _effective_approval_status(request, ttl_sec=repos.approval_ttl_sec, now=now)

    current_level = (
        CircuitBreakerLevel.HALTED
        if control.state == SafetyControlState.ACTIVE
        else CircuitBreakerLevel.NORMAL
    )
    elapsed_sec = int((now - control.created_at).total_seconds()) if control.created_at else 0
    history = _cooldown_history(elapsed_sec=elapsed_sec, cooldown_sec=repos.cooldown_sec)

    cb_state = await repos.circuit_breaker.get_state()
    fresh_outcome = (
        RiskOutcome.DENY if cb_state.level in _REACTIVATABLE_CB_LEVELS else RiskOutcome.ALLOW
    )

    verdict = can_reactivate(
        current_level=current_level,
        metrics_history=history,
        cooldown_sec=repos.cooldown_sec,
        evidence_ref=evidence_ref,
        approval_status=approval_status,
        fresh_risk_outcome=fresh_outcome,
    )
    reason_codes = (
        (_TAXONOMY.get(verdict.reason_code, verdict.reason_code),) if verdict.reason_code else ()
    )

    inputs = _RecoveryInputs(
        tenant_id=tenant_id,
        control_id=control_id,
        control_scope=control.scope.value,
        control_reason=control.reason,
        approval_id=approval_id,
        approval_status=approval_status,
        evidence_ref=evidence_ref,
        current_level=current_level.value,
        cb_level=cb_state.level.value,
        cooldown_sec=repos.cooldown_sec,
        elapsed_since_trip_sec=elapsed_sec,
        as_of=now,
    )
    inputs_hash = sha256_hex(canonical_json(inputs.model_dump(mode="json")))
    fingerprint = f"{trace_id}:{GateKind.RECOVERY.value}:{inputs_hash}"
    decision_id = uuid5(_DECISION_ID_NAMESPACE, fingerprint)
    latency_us = max(1, (time.perf_counter_ns() - start_ns) // 1000)
    rule_result = RuleResult(
        rule_id="recovery_gate",
        outcome=verdict.outcome,
        reason_code=reason_codes[0] if reason_codes else None,
        unit="count",
    )

    decision = RiskDecision(
        decision_id=decision_id,
        gate_kind=GateKind.RECOVERY,
        tenant_id=tenant_id,
        execution_ref=None,
        subject_fingerprint=inputs_hash,
        outcome=verdict.outcome,
        reason_codes=reason_codes,
        obligations=(),
        rule_results=(rule_result,),
        rule_version=_RULE_VERSION,
        rule_hash=_RULE_HASH,
        engine_version=_ENGINE_VERSION,
        inputs_hash=inputs_hash,
        input_refs=(),
        evaluated_at=now,
        expires_at=now + timedelta(seconds=_TTL_SECONDS),
        trace_id=trace_id,
        evidence_ref=evidence_ref,
        latency_us=latency_us,
    )

    await repos.decision_recorder.record(decision, inputs, actor="risk_gate.evaluate_recovery")

    if decision.outcome != RiskOutcome.ALLOW:
        raise RecoveryDeniedError(decision)

    await deactivate_safety_control(
        repos.risk_gate, tenant_id=tenant_id, actor_is_admin=True, control_id=control_id
    )
    return decision


__all__ = ["RecoveryGateRepos", "RecoveryDeniedError", "evaluate_recovery"]
