"""PRE_SUBMIT 게이트 — docs/specs/L4_risk_and_safety_v1.0.md §2/§3.6/§9 R-35.

제출 직전(fenced_submit이 바로 이어 쓴다) 안전 상태 4가지만 재확인하는
얕고 빠른 게이트다: 활성 kill-switch control·circuit breaker level·data
distrust level·connection freshness. 거래 규칙(노출 한도·VaR·상관 등)은
이미 PRE_TRADE에서 평가됐으므로 시그니처에 `OrderIntent`가 없다.

`RiskDecision`(§3.1)을 반환하지만 `RiskRuleBundle`은 쓰지 않는다 — 4개
고정 규칙이라 번들 승인 절차가 불필요하다.

fence 5쌍과 그 pairs에 걸친 활성 control은
`RiskGateRepository.read_fence_and_controls()`로 같은 트랜잭션에서 함께
읽는다 — 반환하는 F0가 이 결정이 근거한 control 상태와 같은 순간의
값이어야 한다(§3.6).

결정은 DENY도 포함해 `RiskDecisionRecorder`(R-25)로 WORM 기록한다.
recorder의 `inputs: BaseModel` 계약에 맞춰 `_PreSubmitInputs`(안전 상태
스냅샷만 담는다 — 가짜 주문 데이터로 `RiskInputs.intent`를 채우지
않는다)를 넘긴다.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import UUID, uuid5

from pydantic import BaseModel

from src.core.risk.decision import GateKind, RiskDecision, RiskOutcome, RuleResult
from src.core.risk.hashing import canonical_json, sha256_hex
from src.foundation.connections.domain.models import ConnectionState, HealthState
from src.foundation.connections.ports.repository import ConnectionRepository
from src.foundation.risk_gate.domain.fence import fence_pairs_for
from src.foundation.risk_gate.domain.models import FenceSnapshot, SafetyControl
from src.foundation.risk_gate.ports.repository import RiskGateRepository
from src.services.risk_decision_recorder import RiskDecisionRecorder

TTL_SECONDS = 2.0
"""§3.3 `decision_ttl.pre_submit_sec` — §3.6 2단계 `now < gate.expires_at`."""

_RULE_VERSION = "risk_gate.pre_submit/1"
_ENGINE_VERSION = "risk_gate.pre_submit/1"
_RULE_IDS = ("active_control", "circuit_breaker", "data_distrust", "connection_fresh")
_RULE_HASH = sha256_hex(
    canonical_json({"gate_kind": "PRE_SUBMIT", "rule_version": _RULE_VERSION, "rules": _RULE_IDS})
)
_DECISION_ID_NAMESPACE = UUID("6f1a6c9a-6e0a-4c9a-9c7b-3f2a2b9d5a10")
_CONNECTED_STATES = (ConnectionState.ACTIVE_READONLY, ConnectionState.DEGRADED)
_CB_DENY_LEVELS = ("restricted", "halted", "emergency")
_DISTRUST_DENY_LEVELS = ("SUSPICIOUS", "DISTRUSTED")


class _PreSubmitInputs(BaseModel, frozen=True):
    """WORM `inputs_snapshot`용 — 이 게이트가 실제로 본 안전 상태만."""

    schema_version: Literal["v1"] = "v1"
    tenant_id: UUID
    execution_ref: str
    provider_code: str
    symbol: str
    circuit_breaker_level: str | None
    data_distrust_level: str | None
    connection_fresh: bool | None
    active_control_scopes: tuple[str, ...]
    fence_snapshot: dict[str, int]
    as_of: datetime


def _result(
    rule_id: str, outcome: RiskOutcome, reason_code: str | None, *, missing: str | None = None
) -> RuleResult:
    return RuleResult(
        rule_id=rule_id,
        outcome=outcome,
        reason_code=reason_code,
        unit="count",
        missing_fields=(missing,) if missing else (),
    )


def _compose(
    *,
    active_controls: tuple[SafetyControl, ...],
    cb_level: str | None,
    distrust_level: str | None,
    connection_fresh: bool | None,
) -> tuple[RiskOutcome, tuple[str, ...], tuple[RuleResult, ...]]:
    # I2 fail-closed — None을 "문제없음"으로 읽지 않는다. 하나라도 결손이면
    # 다른 입력값과 무관하게 즉시 DENY.
    missing = [
        (rule_id, field, value)
        for rule_id, field, value in zip(
            _RULE_IDS[1:],
            ("cb_level", "distrust_level", "connection_fresh"),
            (cb_level, distrust_level, connection_fresh),
            strict=True,
        )
        if value is None
    ]
    if missing:
        results = tuple(
            _result(rule_id, RiskOutcome.DENY, f"RISK_INPUT_MISSING:{field}", missing=field)
            for rule_id, field, _ in missing
        )
        return RiskOutcome.DENY, tuple(r.reason_code for r in results if r.reason_code), results

    if active_controls:
        reasons = tuple(f"RISK_KILL_SWITCH_ACTIVE_{c.scope.value}" for c in active_controls)
        return RiskOutcome.DENY, reasons, (_result("active_control", RiskOutcome.DENY, reasons[0]),)

    if cb_level in _CB_DENY_LEVELS:
        reason = f"RISK_CIRCUIT_BREAKER_{cb_level.upper()}"
        return RiskOutcome.DENY, (reason,), (_result("circuit_breaker", RiskOutcome.DENY, reason),)

    if distrust_level in _DISTRUST_DENY_LEVELS:
        reason = f"RISK_DATA_DISTRUST_{distrust_level}"
        return RiskOutcome.DENY, (reason,), (_result("data_distrust", RiskOutcome.DENY, reason),)

    if not connection_fresh:
        reason = "RISK_INPUT_STALE"
        pause_result = _result("connection_fresh", RiskOutcome.PAUSE, reason)
        return RiskOutcome.PAUSE, (reason,), (pause_result,)

    allow_results = tuple(_result(rule_id, RiskOutcome.ALLOW, None) for rule_id in _RULE_IDS)
    return RiskOutcome.ALLOW, (), allow_results


async def _read_connection_fresh(
    connection_repo: ConnectionRepository, *, tenant_id: UUID, provider_code: str
) -> bool | None:
    connections = await connection_repo.list_connections(tenant_id)
    matching = (
        c
        for c in connections
        if c.provider_code == provider_code and c.state in _CONNECTED_STATES
    )
    connection = next(matching, None)
    if connection is None:
        return None  # 이 provider에 연결 자체가 없음 — I2 결손 취급.
    health = await connection_repo.get_latest_health(connection.id)
    return health is not None and health.state == HealthState.HEALTHY


async def evaluate_pre_submit(
    risk_repo: RiskGateRepository,
    connection_repo: ConnectionRepository,
    decision_recorder: RiskDecisionRecorder,
    *,
    tenant_id: UUID,
    execution_ref: str,
    provider_code: str,
    symbol: str,
    trace_id: UUID,
) -> tuple[RiskDecision, FenceSnapshot]:
    start_ns = time.perf_counter_ns()
    pairs = fence_pairs_for(tenant_id, provider_code, execution_ref)
    fence_snapshot, active_controls = await risk_repo.read_fence_and_controls(pairs)
    cb_level, distrust_level = await risk_repo.read_safety_state(
        provider_code=provider_code, symbol=symbol
    )
    connection_fresh = await _read_connection_fresh(
        connection_repo, tenant_id=tenant_id, provider_code=provider_code
    )

    outcome, reason_codes, rule_results = _compose(
        active_controls=active_controls,
        cb_level=cb_level,
        distrust_level=distrust_level,
        connection_fresh=connection_fresh,
    )

    now = datetime.now(timezone.utc)
    inputs = _PreSubmitInputs(
        tenant_id=tenant_id,
        execution_ref=execution_ref,
        provider_code=provider_code,
        symbol=symbol,
        circuit_breaker_level=cb_level,
        data_distrust_level=distrust_level,
        connection_fresh=connection_fresh,
        active_control_scopes=tuple(c.scope.value for c in active_controls),
        fence_snapshot={
            f"{scope.value}:{ref}": token for (scope, ref), token in fence_snapshot.tokens.items()
        },
        as_of=now,
    )
    inputs_hash = sha256_hex(canonical_json(inputs.model_dump(mode="json")))
    decision_id = uuid5(
        _DECISION_ID_NAMESPACE, f"{trace_id}:{GateKind.PRE_SUBMIT.value}:{inputs_hash}"
    )
    latency_us = max(1, (time.perf_counter_ns() - start_ns) // 1000)

    decision = RiskDecision(
        decision_id=decision_id,
        gate_kind=GateKind.PRE_SUBMIT,
        tenant_id=tenant_id,
        execution_ref=execution_ref,
        subject_fingerprint=inputs_hash,
        outcome=outcome,
        reason_codes=reason_codes,
        obligations=(),
        rule_results=rule_results,
        rule_version=_RULE_VERSION,
        rule_hash=_RULE_HASH,
        engine_version=_ENGINE_VERSION,
        inputs_hash=inputs_hash,
        input_refs=(),
        evaluated_at=now,
        expires_at=now + timedelta(seconds=TTL_SECONDS),
        trace_id=trace_id,
        evidence_ref=None,
        latency_us=latency_us,
    )

    await decision_recorder.record(decision, inputs, actor="risk_gate.evaluate_pre_submit")
    return decision, fence_snapshot
