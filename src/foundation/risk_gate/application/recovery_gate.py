"""Assembly of the RECOVERY gate — L4_risk_and_safety_v1.0.md#§9 R-53, §2 table
row 110, §4.3 CB table, I5 (§8 line 394). Prior art: R-44
`src/core/safety/recovery_gate.py::can_reactivate` (pure judgment; the four
denial rules are not rewritten here), R-35 `evaluate_pre_submit.py` (see for
the assembly pattern).

This leaf only assembles, persists, and records evidence:
1. Look up the safety control (kill switch) by `control_id`. `SafetyControl`
   has no circuit-breaker sub-levels — treat ACTIVE as `HALTED` and INACTIVE
   as `NORMAL` (only whether reactivation applies matters here; this is the
   minimal mapping that satisfies the `CircuitBreakerLevel` the R-44 contract
   requires).
2. Look up the approval request by `approval_id`. Anything past TTL is
   downgraded to "not APPROVED" using the same rule as R-45's
   `circuit_breaker_loop._effective_status` (the clock is only consulted here
   — `can_reactivate` never sees it).
3. cooldown — the `metrics_history` (1 sample per second) persistence table
   does not exist yet (§10 undecided, out of this leaf's scope pending R-54).
   Instead, fill baseline(0) samples for the seconds elapsed since
   `safety_control.created_at` ("last trip") — this stands only for "has
   enough time passed," not evidence that "that interval was measured safe"
   ("unverified": there is no stored measurement history for the past
   interval at all). The measured condition is handled by step 4.
4. fresh re-evaluation — read the current global circuit-breaker level right
   now via `CircuitBreakerService.get_state()`. This simply reuses the value
   that the R-45 tick loop keeps updating from live metrics; this function
   itself does not collect any new metrics.
5. Call `can_reactivate()` with the four inputs above, wrap the result as a
   `RiskDecision`, and record it to WORM via `RiskDecisionRecorder` (R-25) for
   both ALLOW and DENY.
6. Only on ALLOW, call the existing `deactivate_safety_control` (R-23
   deactivation command) — no new deactivation path is created. On DENY,
   raise `RecoveryDeniedError` so the router translates it to 403 via
   EXCEPTION_MAP (the same convention as `RiskGateDeniedError`, see
   start_deployment.py) — raw HTTPException is forbidden.
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
# spec §9 R-53 DoD — the "no evidence" denial is exposed as taxonomy RSK-007.
# Other denial reasons keep can_reactivate's original code as-is (taxonomy undecided).
_TAXONOMY = {"RECOVERY_EVIDENCE_MISSING": "RSK-007"}
_REACTIVATABLE_CB_LEVELS = (CircuitBreakerLevel.HALTED, CircuitBreakerLevel.EMERGENCY)


class RecoveryDeniedError(Exception):
    """A RiskDecision that ended in DENY — the router translates it to 403 via EXCEPTION_MAP."""

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
    """Same rule as R-45's `circuit_breaker_loop._effective_status`."""
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
