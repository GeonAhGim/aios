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
3층: mandate 수치 정책. `require_mandate`(호출부 필수 명시, 기본값 없음 —
   예전 `AIOS_REQUIRE_MANDATE_FOR_SUBMIT` env var 우회를 없앤 지점)로
   "mandate 미연결"의 처리를 정한다. `True`면 `RISK_MANDATE_REQUIRED` DENY
   (`tests/integration/test_order_service_risk_gate.py`가 증명). `False`
   (현재 모든 프로덕션 조립부)면 audit_log만 남기고 통과 — execution 생성
   UI가 아직 `mandate_revision_id`를 연결하지 않기 때문(컬럼은 있음).
   mandate가 있으면 `context.mandate_revision_id`가 현재 active revision과
   일치하는지 먼저 본다(task-1806, fence와 같은 관측-대-현재 패턴) — 불일치면
   `RISK_MANDATE_REVISION_STALE` DENY, 일치해야 `mandates.evaluate_policy()`로
   진행한다.

task-1717 P0-D — 모든 결정을 `_record_decision()`으로 `risk_decision` WORM에
기록해 `GateDecision.decision_id`를 채운다(`GateKind.PRE_SUBMIT`,
`evaluate_pre_submit`의 4-rule 스키마와는 별개 — CB/distrust/connection
필드 없음). mandate를 정식 평가했으면 `policy_decision_id`도, CM-8 컴플라이언스
판정을 했으면 `compliance_decision_id`도 함께 채운다(각각 별개 테이블 참조).
"""
from __future__ import annotations

import time
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Literal
from uuid import UUID, uuid4

import asyncpg
from pydantic import BaseModel

from src.core.event_bus.in_process import InProcessEventBus
from src.core.logging.audit_log import record_audit_log
from src.core.risk.decision import GateKind, RiskDecision, RiskOutcome
from src.core.risk.hashing import canonical_json, sha256_hex
from src.foundation.mandates.adapters.postgres_repository import PostgresMandateRepository
from src.foundation.mandates.application.evaluate_policy import NoActiveMandateError
from src.foundation.mandates.application.evaluate_policy import evaluate as evaluate_mandate_policy
from src.foundation.mandates.contracts.v1 import PolicyEvaluationSubject
from src.foundation.mandates.contracts.v1 import PolicyOutcome as MandateOutcome
from src.foundation.risk_gate.adapters.postgres_decision_repository import (
    PostgresDecisionRepository,
)
from src.foundation.risk_gate.adapters.postgres_repository import PostgresRiskGateRepository
from src.foundation.risk_gate.domain.fence import fence_pairs_for
from src.foundation.risk_gate.domain.models import FenceSnapshot
from src.services.order_service.foundation_compliance import evaluate_compliance_gate
from src.services.order_service.gate import GateDecision, GateOutcome, OrderContext, PreSubmitGate
from src.services.risk_decision_recorder import RiskDecisionRecorder

_RULE_VERSION = "order_service.foundation_gate/1"
# task-1717 — gate() 평가 시점과 실제 claim(INSERT) 사이(fsm 전이·검증 등)의
# 정상적인 지연을 흡수해야 한다. `evaluate_pre_submit`의 TTL_SECONDS=2.0은
# "그 함수 자신이 곧바로 fenced_submit에 이어지는" 설계라 짧지만, 이 게이트는
# 아직 tick 파이프라인 여러 단계를 거친 뒤에야 claim에 도달하므로 더 넉넉하게
# 잡는다 — 그래도 초 단위로 짧아 보안적으로 무의미하지 않다.
_TTL_SECONDS = 30.0


class _GateInputs(BaseModel, frozen=True):
    """WORM `inputs_snapshot`용 — `decision_binding.verify_decision_binding`이
    요구하는 최소 계약(top-level symbol/side/quantity/fence_snapshot)만
    채운다. `evaluate_pre_submit._PreSubmitInputs`와 스키마가 다르다(이
    게이트는 CB/distrust/connection을 보지 않는다, 위 모듈 docstring)."""

    schema_version: Literal["v1"] = "v1"
    tenant_id: UUID
    execution_ref: str
    exchange: str
    symbol: str | None
    side: str | None
    quantity: Decimal | None
    mandate_revision_id: UUID | None
    reason_codes: tuple[str, ...]
    fence_snapshot: dict[str, int]
    as_of: datetime


def _flatten_fence(snapshot: FenceSnapshot) -> dict[str, int]:
    return {f"{scope.value}:{ref}": token for (scope, ref), token in snapshot.tokens.items()}


def _is_stale(observed: Mapping[str, int], current: Mapping[str, int]) -> bool:
    """§3.6 stale 정의 — 토큰 증가만 stale로 본다. `observed`에만 있고
    `current`에 없는 pair는 없다(같은 `fence_pairs_for` 5쌍을 항상 읽는다)."""
    return any(current.get(pair, 0) > observed_token for pair, observed_token in observed.items())


async def _record_decision(
    recorder: RiskDecisionRecorder, *, context: OrderContext, outcome: GateOutcome,
    reason_codes: tuple[str, ...], fence: Mapping[str, int], start_ns: int,
) -> UUID:
    now = datetime.now(timezone.utc)
    # task-2395 — measured from gate() entry, same max(1, ...) convention as evaluator.py.
    latency_us = max(1, (time.perf_counter_ns() - start_ns) // 1000)
    execution_ref = f"exec:{context.execution_id}"
    inputs = _GateInputs(
        tenant_id=context.user_id,
        execution_ref=execution_ref,
        exchange=context.exchange,
        symbol=context.symbol,
        side=context.side,
        quantity=context.quantity,
        mandate_revision_id=context.mandate_revision_id,
        reason_codes=reason_codes,
        fence_snapshot=dict(fence),
        as_of=now,
    )
    inputs_hash = sha256_hex(canonical_json(inputs.model_dump(mode="json")))
    decision_id = uuid4()
    risk_outcome = RiskOutcome.ALLOW if outcome == GateOutcome.ALLOW else RiskOutcome.DENY
    decision = RiskDecision(
        decision_id=decision_id,
        gate_kind=GateKind.PRE_SUBMIT,
        tenant_id=context.user_id,
        execution_ref=execution_ref,
        subject_fingerprint=inputs_hash,
        outcome=risk_outcome,
        reason_codes=reason_codes,
        obligations=(),
        rule_results=(),
        rule_version=_RULE_VERSION,
        rule_hash=sha256_hex(canonical_json({"rule_version": _RULE_VERSION})),
        engine_version=_RULE_VERSION,
        inputs_hash=inputs_hash,
        input_refs=(),
        evaluated_at=now,
        expires_at=now + timedelta(seconds=_TTL_SECONDS),
        trace_id=uuid4(),
        evidence_ref=None,
        latency_us=latency_us,
    )
    await recorder.record(decision, inputs, actor="order_service.foundation_gate")
    return decision_id


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
) -> PreSubmitGate:
    risk_repo = PostgresRiskGateRepository(pool)
    mandate_repo = PostgresMandateRepository(pool)
    recorder = RiskDecisionRecorder(pool, PostgresDecisionRepository(pool), InProcessEventBus())

    async def gate(context: OrderContext) -> GateDecision:
        start_ns = time.perf_counter_ns()
        pairs = fence_pairs_for(context.user_id, context.exchange, f"exec:{context.execution_id}")
        fence_snapshot, active_controls = await risk_repo.read_fence_and_controls(pairs)
        fence = _flatten_fence(fence_snapshot)

        if context.observed_fence is not None and _is_stale(context.observed_fence, fence):
            decision_id = await _record_decision(
                recorder, context=context, outcome=GateOutcome.DENY,
                reason_codes=("RISK_FENCE_STALE",), fence=fence, start_ns=start_ns,
            )
            return GateDecision(
                outcome=GateOutcome.DENY, reason_codes=("RISK_FENCE_STALE",),
                fence_snapshot=fence, decision_id=decision_id,
            )

        if active_controls:
            reason_codes = tuple(
                f"RISK_KILL_SWITCH_ACTIVE_{c.scope.value}" for c in active_controls
            )
            decision_id = await _record_decision(
                recorder, context=context, outcome=GateOutcome.DENY,
                reason_codes=reason_codes, fence=fence, start_ns=start_ns,
            )
            return GateDecision(
                outcome=GateOutcome.DENY, reason_codes=reason_codes,
                fence_snapshot=fence, decision_id=decision_id,
            )

        # CM-8/CM-A5 — evaluated here but only consumed at the two ALLOW
        # points below, so existing risk/numeric-mandate DENY reason codes
        # keep their own specific reason; compliance only gets the final say
        # when this function was about to return ALLOW anyway.
        compliance = await evaluate_compliance_gate(
            mandate_repo, context,
            require_compliance_mandate=require_compliance_mandate,
            now=datetime.now(timezone.utc),
        )

        async def _finish_allow(*, policy_decision_id: UUID | None = None) -> GateDecision:
            if not compliance.allowed:
                cid = await _record_decision(
                    recorder, context=context, outcome=GateOutcome.DENY,
                    reason_codes=compliance.reason_codes, fence=fence, start_ns=start_ns,
                )
                return GateDecision(
                    outcome=GateOutcome.DENY, reason_codes=compliance.reason_codes,
                    fence_snapshot=fence, decision_id=cid,
                    compliance_decision_id=compliance.compliance_decision_id,
                )
            cid = await _record_decision(
                recorder, context=context, outcome=GateOutcome.ALLOW, reason_codes=(),
                fence=fence, start_ns=start_ns,
            )
            return GateDecision(
                outcome=GateOutcome.ALLOW, fence_snapshot=fence, decision_id=cid,
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
                decision_id = await _record_decision(
                    recorder, context=context, outcome=GateOutcome.DENY,
                    reason_codes=("RISK_MANDATE_REQUIRED",), fence=fence, start_ns=start_ns,
                )
                return GateDecision(
                    outcome=GateOutcome.DENY, reason_codes=("RISK_MANDATE_REQUIRED",),
                    fence_snapshot=fence, decision_id=decision_id,
                )
            return await _finish_allow()

        # task-1806 — applies the same observed-vs-current pattern as R-36
        # to the mandate revision too: `context.mandate_revision_id` is the
        # revision this execution last bound to (observed). If a new
        # revision was activated by an amendment in the meantime (the prior
        # revision becomes SUPERSEDED), `evaluate_mandate_policy` always
        # evaluates against the "current" active revision, so passing
        # through without checking would silently re-evaluate the execution
        # against rules it never agreed to (looser or stricter) — the same
        # class of defect as fence staleness. On mismatch, deny and require
        # rebinding.
        mandate = await mandate_repo.get_mandate(context.user_id)
        if mandate is None or mandate.active_revision_id != context.mandate_revision_id:
            decision_id = await _record_decision(
                recorder, context=context, outcome=GateOutcome.DENY,
                reason_codes=("RISK_MANDATE_REVISION_STALE",), fence=fence, start_ns=start_ns,
            )
            return GateDecision(
                outcome=GateOutcome.DENY, reason_codes=("RISK_MANDATE_REVISION_STALE",),
                fence_snapshot=fence, decision_id=decision_id,
            )

        try:
            mandate_decision = await evaluate_mandate_policy(
                mandate_repo,
                tenant_id=context.user_id,
                subject=PolicyEvaluationSubject(command_type="LEGACY_ORDER_SUBMIT"),
            )
        except NoActiveMandateError:
            decision_id = await _record_decision(
                recorder, context=context, outcome=GateOutcome.DENY,
                reason_codes=("RISK_INPUT_MANDATE_MISSING",), fence=fence, start_ns=start_ns,
            )
            return GateDecision(
                outcome=GateOutcome.DENY, reason_codes=("RISK_INPUT_MANDATE_MISSING",),
                fence_snapshot=fence, decision_id=decision_id,
            )

        if mandate_decision.outcome != MandateOutcome.ALLOW:
            reason_codes = tuple(mandate_decision.reason_codes)
            decision_id = await _record_decision(
                recorder, context=context, outcome=GateOutcome.DENY,
                reason_codes=reason_codes, fence=fence, start_ns=start_ns,
            )
            return GateDecision(
                outcome=GateOutcome.DENY, reason_codes=reason_codes, fence_snapshot=fence,
                decision_id=decision_id, policy_decision_id=mandate_decision.id,
            )
        return await _finish_allow(policy_decision_id=mandate_decision.id)

    return gate
