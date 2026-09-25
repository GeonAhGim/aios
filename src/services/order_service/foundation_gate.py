"""`submit_order(pre_submit_gate=...)`/`ExecutionService(pre_start_gate=...)`의
실제 구현체 — foundation risk_gate/mandates를 여기서만 import한다
(`gate.py`/`submit.py`는 foundation을 모른다, PM 지침).

R-36 — 원자적 fence+control 읽기(`fence_pairs_for` + `read_fence_and_controls`,
같은 REPEATABLE READ 트랜잭션)에 위임해 GLOBAL/TENANT/ACCOUNT/PROVIDER/
STRATEGY_DEPLOYMENT 5쌍 전부를 보고, F0을 `GateDecision.fence_snapshot`으로
관통시킨다(R-33). `evaluate_pre_submit`(CB/data-distrust/connection-freshness)
은 아직 위임하지 않는다 — Foundation onboarding을 거치지 않은 legacy PAPER
실행이 즉시 fail-closed DENY로 막히기 때문(별도 리프, 미검증 스코프 밖).

3단 게이트(순서대로 평가, 먼저 DENY가 나오면 그 자리에서 반환):
1층: fence stale(§3.6) 또는 활성 control → 즉시 DENY.
2층(CM-8/CM-A5): `evaluate_compliance_gate` — mandate 위임장 규칙(CM-6/7)
   위반은 리스크·수치정책이 ALLOW여도 DENY(권위 분리). `require_compliance_
   mandate`가 "mandate 자체가 없을 때"의 처리를 정한다(기본 False — 아래
   `require_mandate`와 같은 이유).
Layer 3: mandate numeric policy (`foundation_gate_mandate_layer.
   evaluate_mandate_layer`). `require_mandate` (mandatory explicit
   argument, no default) decides "no mandate bound" handling -- `True` ->
   `RISK_MANDATE_REQUIRED` DENY, `False` -> pass with only an audit_log
   entry. Since H-1b (task-3369) all three production assembly sites pass
   `True`; the UI-unfilled `mandate_revision_id` is filled on entry by
   `foundation_mandate_resolution.with_resolved_mandate()` (H-1a resolver).
   Once present, a mismatch vs. the current active revision (task-1806,
   observed-vs-current, same as fence) is `RISK_MANDATE_REVISION_STALE`
   DENY; a match proceeds to `mandates.evaluate_policy()`.

task-1717 P0-D -- every decision is recorded to the `risk_decision` WORM via
`record_decision()`, filling `GateDecision.decision_id` (`GateKind.
PRE_SUBMIT`, a separate schema from `evaluate_pre_submit`'s 4-rule one --
no CB/distrust/connection fields). If mandate was actually evaluated,
`policy_decision_id` is also filled; if a CM-8 compliance verdict was made,
`compliance_decision_id` is filled too (each references its own table).

task-3986 — layer 4, `foundation_personal_gate.evaluate_personal_layer`, is
only evaluated once layers 1-3 all ALLOW (`_finish_allow`). It only acts
when personal mode is actually scoped to this account/tenant
(`personal_state.personal_mode_account_id()`) -- every other account is
untouched (no global enforcement; fixes the "denies nothing" defect QA
task-3819 found).

task-4006 -- P6 LOC split (logic move only, 4-layer evaluation order and
fail-closed invariants unchanged): WORM `risk_decision` recording
(`record_decision`/`flatten_fence`/`is_stale`) moved to
`foundation_gate_decision.py`; layer 3 (mandate numeric policy) moved to
`foundation_gate_mandate_layer.py`.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from uuid import UUID

import asyncpg

from src.core.event_bus.in_process import InProcessEventBus
from src.core.logging.audit_log import record_audit_log
from src.foundation.mandates.adapters.postgres_repository import PostgresMandateRepository
from src.foundation.risk.adapters.json_state_store import JsonPersonalStateStore
from src.foundation.risk.adapters.telegram_adapter import TelegramNotifierAdapter
from src.foundation.risk.ports.notifier import PersonalNotifierPort
from src.foundation.risk.ports.state import PersonalOperationStatePort
from src.foundation.risk_gate.adapters.postgres_decision_repository import (
    PostgresDecisionRepository,
)
from src.foundation.risk_gate.adapters.postgres_repository import PostgresRiskGateRepository
from src.foundation.risk_gate.domain.fence import fence_pairs_for
from src.services.order_service.foundation_compliance import evaluate_compliance_gate
from src.services.order_service.foundation_gate_decision import (
    flatten_fence,
    is_stale,
    record_decision,
)
from src.services.order_service.foundation_gate_mandate_layer import evaluate_mandate_layer
from src.services.order_service.foundation_mandate_resolution import with_resolved_mandate
from src.services.order_service.foundation_personal_gate import evaluate_personal_layer
from src.services.order_service.gate import GateDecision, GateOutcome, OrderContext, PreSubmitGate
from src.services.risk_decision_recorder import RiskDecisionRecorder


def make_foundation_pre_submit_gate(
    pool: asyncpg.Pool,
    *,
    require_mandate: bool,
    # CM-8 — sibling flag for the independent compliance rule-bundle check
    # (`evaluate_compliance_gate`); only governs "no mandate configured at
    # all" (same reasoning/default as `require_mandate`, no UI binds mandates
    # to executions yet). CM-A5 (mandate violations block even risk-ALLOW
    # orders) is enforced unconditionally, regardless of this flag.
    require_compliance_mandate: bool = False,
    # task-3986 — the personal-conservative 4th layer's own ports. Default
    # to the real single-operator adapters (cheap, I/O-free __init__, so
    # constructing them here can never fail the whole factory -- see
    # foundation_personal_gate.py's module docstring on why a broken bundle
    # config still only denies the one scoped account, not this factory
    # call). None of the three production assembly sites pass these
    # explicitly today, so this is purely additive for every other account.
    personal_state: PersonalOperationStatePort | None = None,
    personal_notifier: PersonalNotifierPort | None = None,
) -> PreSubmitGate:
    risk_repo = PostgresRiskGateRepository(pool)
    mandate_repo = PostgresMandateRepository(pool)
    recorder = RiskDecisionRecorder(pool, PostgresDecisionRepository(pool), InProcessEventBus())
    p_state = personal_state if personal_state is not None else JsonPersonalStateStore()
    p_notifier = personal_notifier if personal_notifier is not None else TelegramNotifierAdapter()

    async def gate(context: OrderContext) -> GateDecision:
        start_ns = time.perf_counter_ns()
        context = await with_resolved_mandate(pool, context)  # H-1b
        pairs = fence_pairs_for(context.user_id, context.exchange, f"exec:{context.execution_id}")
        fence_snapshot, active_controls = await risk_repo.read_fence_and_controls(pairs)
        fence = flatten_fence(fence_snapshot)

        if context.observed_fence is not None and is_stale(context.observed_fence, fence):
            decision_id = await record_decision(
                recorder,
                context=context,
                outcome=GateOutcome.DENY,
                reason_codes=("RISK_FENCE_STALE",),
                fence=fence,
                start_ns=start_ns,
            )
            return GateDecision(
                outcome=GateOutcome.DENY,
                reason_codes=("RISK_FENCE_STALE",),
                fence_snapshot=fence,
                decision_id=decision_id,
            )

        if active_controls:
            reason_codes = tuple(
                f"RISK_KILL_SWITCH_ACTIVE_{c.scope.value}" for c in active_controls
            )
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
            )

        # CM-8/CM-A5 — evaluated here but only consumed at the two ALLOW
        # points below, so existing risk/numeric-mandate DENY reason codes
        # keep their own specific reason; compliance only gets the final say
        # when this function was about to return ALLOW anyway.
        compliance = await evaluate_compliance_gate(
            mandate_repo,
            context,
            require_compliance_mandate=require_compliance_mandate,
            now=datetime.now(timezone.utc),
        )

        async def _finish_allow(*, policy_decision_id: UUID | None = None) -> GateDecision:
            if not compliance.allowed:
                cid = await record_decision(
                    recorder,
                    context=context,
                    outcome=GateOutcome.DENY,
                    reason_codes=compliance.reason_codes,
                    fence=fence,
                    start_ns=start_ns,
                )
                return GateDecision(
                    outcome=GateOutcome.DENY,
                    reason_codes=compliance.reason_codes,
                    fence_snapshot=fence,
                    decision_id=cid,
                    compliance_decision_id=compliance.compliance_decision_id,
                )
            # layer 4 (task-3986) -- only reached when layers 1-3 all ALLOW.
            # If personal mode is not scoped to this account,
            # evaluate_personal_layer returns a no-op (_ALLOW) immediately,
            # so every other account is unaffected.
            personal = await evaluate_personal_layer(
                context, personal_state=p_state, personal_notifier=p_notifier
            )
            if personal.denied:
                cid = await record_decision(
                    recorder,
                    context=context,
                    outcome=GateOutcome.DENY,
                    reason_codes=personal.reason_codes,
                    fence=fence,
                    start_ns=start_ns,
                )
                return GateDecision(
                    outcome=GateOutcome.DENY,
                    reason_codes=personal.reason_codes,
                    fence_snapshot=fence,
                    decision_id=cid,
                    compliance_decision_id=compliance.compliance_decision_id,
                )
            cid = await record_decision(
                recorder,
                context=context,
                outcome=GateOutcome.ALLOW,
                reason_codes=(),
                fence=fence,
                start_ns=start_ns,
            )
            return GateDecision(
                outcome=GateOutcome.ALLOW,
                fence_snapshot=fence,
                decision_id=cid,
                policy_decision_id=policy_decision_id,
                compliance_decision_id=compliance.compliance_decision_id,
            )

        if context.mandate_revision_id is None:
            async with pool.acquire() as conn:
                await record_audit_log(
                    conn,
                    actor_agent="order_service.submit",
                    action_type="risk_gate.unmandated_submit",
                    user_id=context.user_id,
                    target_type="strategy_execution",
                    target_id=str(context.execution_id),
                    decision_data={"exchange": context.exchange},
                )
            if require_mandate:
                decision_id = await record_decision(
                    recorder,
                    context=context,
                    outcome=GateOutcome.DENY,
                    reason_codes=("RISK_MANDATE_REQUIRED",),
                    fence=fence,
                    start_ns=start_ns,
                )
                return GateDecision(
                    outcome=GateOutcome.DENY,
                    reason_codes=("RISK_MANDATE_REQUIRED",),
                    fence_snapshot=fence,
                    decision_id=decision_id,
                )
            return await _finish_allow()

        # task-1806 (layer 3 detail, see foundation_gate_mandate_layer.py) --
        # observed-vs-current mandate revision check, then numeric policy.
        return await evaluate_mandate_layer(
            mandate_repo=mandate_repo,
            recorder=recorder,
            context=context,
            fence=fence,
            start_ns=start_ns,
            finish_allow=_finish_allow,
        )

    return gate
