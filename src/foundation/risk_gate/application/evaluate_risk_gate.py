"""EvaluateRiskGate command (shared by the DEPLOYMENT/PRE_INTENT gates).

Spec: AIOSproject #48 §3, #78 §2/§3.

#71 §4 Contract ownership — risk_gate only consumes mandate PolicyDecision
and connection health as "input whose judgment is already final". Reusing
mandates.evaluate_policy() as-is is the design intent mandates' own
docstring states ("other bounded contexts (risk_gate, ...) consume mandate
judgment only through this function").
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from src.core.observability.context import current as current_request_context
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
"""#78 §2 "ALLOW expires rapidly" — shorter than mandates' (30s). Because
risk_gate is the final veto, it must not reuse a stale ALLOW any longer
after a mandate changes."""

_DEPLOYMENT_CHECK_SUBJECT = PolicyEvaluationSubject(command_type="RISK_GATE_DEPLOYMENT_CHECK")


class CrossTenantConnectionReferenceError(Exception):
    """An attempt to slip another tenant's connection_id into this tenant's
    gate evaluation — rejected without leaking whether it exists."""


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
        trace_id=evaluation.trace_id,
    )


async def _mandate_state_marker(mandate_repo: MandateRepository, tenant_id: UUID) -> str:
    """H-11 (same defect class as 0598fdab, here risk_gate's own
    `EVALUATION_CACHE_TTL_SECONDS` cache) — if `subject_fingerprint` does not
    reflect mandate state, a cached ALLOW can keep being returned until its
    TTL expires even after the mandate transitions via activate/pause/resume.
    The fix `evaluate_policy.py`'s `_fingerprint()` already proved (including
    revision id+state in the fingerprint) is applied here the same way --
    even if a separate invalidation call (the router's
    `invalidate_evaluations()`, etc.) is forgotten, a cache miss naturally
    occurs the instant (0ms) the mandate changes, so those explicit calls are
    now just a safety net, not a precondition for correctness."""
    mandate = await mandate_repo.get_mandate(tenant_id)
    if mandate is None or mandate.active_revision_id is None:
        return "NO_MANDATE"
    revision = await mandate_repo.get_revision(mandate.active_revision_id)
    if revision is None:
        return "NO_MANDATE"
    return f"{revision.id}:{revision.state.value}"


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
    mandate_state_marker = await _mandate_state_marker(mandate_repo, tenant_id)
    fingerprint_payload = (
        f"{connection_id}|{plan.model_dump_json() if plan is not None else ''}"
        f"|{mandate_state_marker}"
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
    provider_code: str | None = None
    if connection_id is not None:
        connection = await connection_repo.get_connection(connection_id)
        if connection is None or connection.tenant_id != tenant_id:
            raise CrossTenantConnectionReferenceError(str(connection_id))
        health = await connection_repo.get_latest_health(connection_id)
        connection_fresh = health is not None and health.state.value == "HEALTHY"
        provider_code = connection.provider_code

    # Red team #2026-09-02-27 — if provider_code is not passed, a PROVIDER-scoped
    # safety control would never be looked up here (referencing
    # list_active_controls' provider_code=None default) -- if a connection
    # exists, its provider's controls must also be checked.
    active_controls = await repo.list_active_controls(
        tenant_id=tenant_id, provider_code=provider_code
    )

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
    # PLT-01 §3.1 — a new evaluation row always stamps the trace_id of the
    # current request/system context (f4b9d6e5a7c8, §3.8 tracing). Even if a
    # context was never bound, `current()` returns a fail-open fallback
    # value, so this never collapses to None here -- the fail-closed
    # decision (DENY on missing input, etc.) has already finished above.
    trace_id = current_request_context().trace_id
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
            trace_id=trace_id,
        )
    )
    return _evaluation_to_view(evaluation)
