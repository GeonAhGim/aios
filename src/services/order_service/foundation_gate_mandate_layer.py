"""`foundation_gate.py`의 3층(mandate numeric policy) 평가 — task-4006, P6
LOC 분할로 책임 분리(로직 이동만, 평가 순서·fail-closed 불변식은 그대로).

task-1806 — R-36의 observed-vs-current 패턴을 mandate revision에도 적용한다:
`context.mandate_revision_id`는 이 execution이 마지막으로 바인딩한 revision
(observed)이다. 그 사이 amendment로 새 revision이 활성화됐다면(이전 revision은
SUPERSEDED로 전이) `evaluate_mandate_policy`는 항상 "현재" 활성 revision을
기준으로 평가하므로, 확인 없이 통과시키면 이 execution이 동의한 적 없는
규칙(더 느슨하거나 엄격한)으로 조용히 재평가하게 된다 — fence staleness와
같은 결함 부류다. 불일치 시 거부하고 재바인딩을 요구한다.
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
