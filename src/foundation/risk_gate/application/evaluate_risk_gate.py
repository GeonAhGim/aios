"""EvaluateRiskGate 커맨드(DEPLOYMENT/PRE_INTENT 게이트 공용).

Spec: AIOSproject 48번 §3, 78번 §2/§3.

71번 §4 Contract ownership — risk_gate는 mandate PolicyDecision과 connection
health를 "이미 판단이 끝난 입력"으로만 소비한다. mandates.evaluate_policy()를
그대로 재사용하는 건 mandates 자신의 docstring이 명시한 설계 의도다("다른
bounded context(risk_gate, ...)는 이 함수를 통해서만 mandate 판단을
소비한다").
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from src.foundation.connections.ports.repository import ConnectionRepository
from src.foundation.mandates.application.evaluate_policy import NoActiveMandateError
from src.foundation.mandates.application.evaluate_policy import evaluate as evaluate_mandate_policy
from src.foundation.mandates.contracts.v1 import PolicyEvaluationSubject
from src.foundation.mandates.contracts.v1 import PolicyOutcome as MandatePolicyOutcome
from src.foundation.mandates.ports.repository import MandateRepository
from src.foundation.risk_gate.contracts.v1 import GateKind as ContractGateKind
from src.foundation.risk_gate.contracts.v1 import RiskEvaluationView
from src.foundation.risk_gate.contracts.v1 import RiskOutcome as ContractOutcome
from src.foundation.risk_gate.domain.models import GateKind, RiskEvaluation, RiskEvaluationInput
from src.foundation.risk_gate.domain.rules import (
    RULE_VERSION,
    compute_subject_fingerprint,
    evaluate_risk,
)
from src.foundation.risk_gate.ports.repository import RiskGateRepository

EVALUATION_CACHE_TTL_SECONDS = 10
"""78번 §2 "ALLOW expires rapidly" — mandates(30초)보다 짧다. risk_gate는
최종 veto라 mandate가 바뀐 뒤 더 오래 stale ALLOW를 재사용하면 안 된다."""

_DEPLOYMENT_CHECK_SUBJECT = PolicyEvaluationSubject(command_type="RISK_GATE_DEPLOYMENT_CHECK")


class CrossTenantConnectionReferenceError(Exception):
    """다른 tenant의 connection_id를 이 tenant의 게이트 평가에 끼워 넣으려는
    시도 — 존재 여부를 흘리지 않고 거부한다."""


def _evaluation_to_view(evaluation: RiskEvaluation) -> RiskEvaluationView:
    return RiskEvaluationView(
        id=evaluation.id,
        gate_kind=ContractGateKind(evaluation.gate_kind.value),
        outcome=ContractOutcome(evaluation.outcome.value),
        reason_codes=list(evaluation.reason_codes),
        obligations=list(evaluation.obligations),
        rule_version=evaluation.rule_version,
        evaluated_at=evaluation.evaluated_at,
        expires_at=evaluation.expires_at,
    )


async def evaluate_risk_gate(
    repo: RiskGateRepository,
    mandate_repo: MandateRepository,
    connection_repo: ConnectionRepository,
    *,
    tenant_id: UUID,
    gate_kind: GateKind,
    connection_id: UUID | None = None,
    plan: PolicyEvaluationSubject | None = None,
) -> RiskEvaluationView:
    fingerprint_payload = (
        f"{connection_id}|{plan.model_dump_json() if plan is not None else ''}"
    )
    fingerprint = compute_subject_fingerprint(str(tenant_id), gate_kind.value, fingerprint_payload)

    cached = await repo.get_cached_evaluation(tenant_id, fingerprint)
    if cached is not None:
        return _evaluation_to_view(cached)

    try:
        mandate_decision = await evaluate_mandate_policy(
            mandate_repo, tenant_id=tenant_id, subject=plan or _DEPLOYMENT_CHECK_SUBJECT
        )
        mandate_available = True
        mandate_blocking = mandate_decision.outcome != MandatePolicyOutcome.ALLOW
        mandate_reason_codes = tuple(mandate_decision.reason_codes)
    except NoActiveMandateError:
        mandate_available = False
        mandate_blocking = False
        mandate_reason_codes = ()

    connection_fresh: bool | None = None
    if connection_id is not None:
        connection = await connection_repo.get_connection(connection_id)
        if connection is None or connection.tenant_id != tenant_id:
            raise CrossTenantConnectionReferenceError(str(connection_id))
        health = await connection_repo.get_latest_health(connection_id)
        connection_fresh = health is not None and health.state.value == "HEALTHY"

    active_controls = await repo.list_active_controls(tenant_id=tenant_id)

    outcome, reasons, obligations = evaluate_risk(
        RiskEvaluationInput(
            mandate_available=mandate_available,
            mandate_blocking=mandate_blocking,
            mandate_reason_codes=mandate_reason_codes,
            connection_fresh=connection_fresh,
            active_controls=active_controls,
        )
    )

    now = datetime.now(timezone.utc)
    evaluation = await repo.insert_evaluation(
        RiskEvaluation(
            id=uuid4(),
            tenant_id=tenant_id,
            gate_kind=gate_kind,
            subject_fingerprint=fingerprint,
            outcome=outcome,
            reason_codes=tuple(reasons),
            obligations=tuple(obligations),
            rule_version=RULE_VERSION,
            evaluated_at=now,
            expires_at=now + timedelta(seconds=EVALUATION_CACHE_TTL_SECONDS),
        )
    )
    return _evaluation_to_view(evaluation)
