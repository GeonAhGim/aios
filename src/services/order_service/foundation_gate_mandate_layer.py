"""Layer 3 (mandate numeric policy) evaluation for `foundation_gate.py` --
split out for LOC (task-4006, P6, logic move only, evaluation order and
fail-closed invariants unchanged).

task-1806 -- applies the same observed-vs-current pattern as R-36 to the
mandate revision too: `context.mandate_revision_id` is the revision this
execution last bound to (observed). If a new revision was activated by an
amendment in the meantime (the prior revision becomes SUPERSEDED),
`evaluate_mandate_policy` always evaluates against the "current" active
revision, so passing through without checking would silently re-evaluate
the execution against rules it never agreed to (looser or stricter) -- the
same class of defect as fence staleness. On mismatch, deny and require
rebinding.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping

from src.foundation.mandates.application.evaluate_policy import NoActiveMandateError
from src.foundation.mandates.application.evaluate_policy import evaluate as evaluate_mandate_policy
from src.foundation.mandates.contracts.v1 import PolicyEvaluationSubject
from src.foundation.mandates.contracts.v1 import PolicyOutcome as MandateOutcome
from src.foundation.mandates.ports.repository import MandateRepository
from src.services.order_service.foundation_gate_decision import record_decision
from src.services.order_service.gate import GateDecision, GateOutcome, OrderContext
from src.services.risk_decision_recorder import RiskDecisionRecorder

FinishAllow = Callable[..., Awaitable[GateDecision]]


async def evaluate_mandate_layer(
    *,
    mandate_repo: MandateRepository,
    recorder: RiskDecisionRecorder,
    context: OrderContext,
    fence: Mapping[str, int],
    start_ns: int,
    finish_allow: FinishAllow,
) -> GateDecision:
    mandate = await mandate_repo.get_mandate(context.user_id)
    if mandate is None or mandate.active_revision_id != context.mandate_revision_id:
        decision_id = await record_decision(
            recorder,
            context=context,
            outcome=GateOutcome.DENY,
            reason_codes=("RISK_MANDATE_REVISION_STALE",),
            fence=fence,
            start_ns=start_ns,
        )
        return GateDecision(
            outcome=GateOutcome.DENY,
            reason_codes=("RISK_MANDATE_REVISION_STALE",),
            fence_snapshot=fence,
            decision_id=decision_id,
        )

    try:
        mandate_decision = await evaluate_mandate_policy(
            mandate_repo,
            tenant_id=context.user_id,
            subject=PolicyEvaluationSubject(command_type="LEGACY_ORDER_SUBMIT"),
        )
    except NoActiveMandateError:
        decision_id = await record_decision(
            recorder,
            context=context,
            outcome=GateOutcome.DENY,
            reason_codes=("RISK_INPUT_MANDATE_MISSING",),
            fence=fence,
            start_ns=start_ns,
        )
        return GateDecision(
            outcome=GateOutcome.DENY,
            reason_codes=("RISK_INPUT_MANDATE_MISSING",),
            fence_snapshot=fence,
            decision_id=decision_id,
        )

    if mandate_decision.outcome != MandateOutcome.ALLOW:
        reason_codes = tuple(mandate_decision.reason_codes)
        decision_id = await record_decision(
            recorder,
            context=context,
            outcome=GateOutcome.DENY,
            reason_codes=reason_codes,
            fence=fence,
            start_ns=start_ns,
        )
        return GateDecision(
            outcome=GateOutcome.DENY,
            reason_codes=reason_codes,
            fence_snapshot=fence,
            decision_id=decision_id,
            policy_decision_id=mandate_decision.id,
        )
    return await finish_allow(policy_decision_id=mandate_decision.id)
