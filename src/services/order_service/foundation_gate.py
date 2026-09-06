"""`submit_order(pre_submit_gate=...)`/`ExecutionService(pre_start_gate=...)`의
실제 구현체 — foundation risk_gate/mandates를 여기서만 import한다
(`gate.py`/`submit.py`는 foundation을 모른다, PM 지침).

R-36 — R-35 `evaluate_pre_submit`(task-1362, d103d07)이 도입한 원자적
fence+control 읽기(`fence_pairs_for` + `read_fence_and_controls`)에
위임한다: 기존 `list_active_controls(tenant_id, provider_code)`는
GLOBAL/TENANT/ACCOUNT/PROVIDER 4쌍만 봤고 STRATEGY_DEPLOYMENT 범위
킬스위치를 놓쳤다 — `fence_pairs_for`의 5쌍 전부를 같은 트랜잭션
(REPEATABLE READ)에서 함께 읽어 그 결손을 없애고, 동시에 F0(fence
snapshot)를 확보해 `GateDecision.fence_snapshot`으로 호출부까지 그대로
넘긴다(R-33 fence 관통). `evaluate_pre_submit` 자체(CB/data-distrust/
connection-freshness)는 아직 위임하지 않는다 — 그 3개 입력은 Foundation
onboarding(`account_connections`)을 거친 tenant만 값을 가지는데, legacy
PAPER 실행 전부가 아직 그 온보딩을 거치지 않아 그대로 위임하면 모든 legacy
주문이 "입력 결손 → fail-closed DENY"로 즉시 막힌다 — 별도 리프에서
onboarding 이관과 함께 다뤄야 한다(미검증 스코프 밖, 이 파일 docstring에
남겨 둔다).

2단 게이트:
1층(항상 검사): fence가 stale하면(§3.6 관측된 F0보다 현재 토큰이 크면)
   즉시 DENY — 이번 평가 근거가 이미 낡았다는 뜻이라 그 아래 판단을
   신뢰할 수 없다. 다음으로 GLOBAL/TENANT/ACCOUNT/PROVIDER/이 실행 범위에
   활성 control이 하나라도 있으면 mandate 유무와 무관하게 DENY.
2층: mandate가 없을 때의 처리는 `require_mandate`(호출부가 반드시 명시,
   기본값 없음 — 이 자체가 예전 `AIOS_REQUIRE_MANDATE_FOR_SUBMIT` env var
   우회 경로를 없앤 지점이다: env var는 배포 시점에 코드 리뷰 없이 조용히
   뒤집을 수 있었지만, 이제는 호출부 코드에 `True`/`False`가 그대로
   드러난다)로 정해진다.
   - `require_mandate=True`: mandate 미연결이면 `RISK_MANDATE_REQUIRED`
     DENY(I-01 fail-closed, RSK-002 완전 적용). 이 경로의 정확성은
     `tests/integration/test_order_service_risk_gate.py`가 독립적으로
     증명한다.
   - `require_mandate=False`(현재 두 프로덕션 조립부 `background_loops.py`
     pre_submit_gate·`execution_deps.py` pre_start_gate 전부 이 값):
     mandate 미연결이어도 audit_log만 남기고 통과 — execution 생성 UI가
     아직 어떤 execution에도 `mandate_revision_id`를 연결하지 않는다
     (컬럼은 있지만 채우는 경로가 없음). 지금 `True`로 뒤집으면 legacy
     PAPER/LIVE 실행 전체가 이번 tick부터 예외 없이 막히는 회귀가 된다
     — mandate-연결 UI가 나오면 그때 두 조립부를 `True`로 뒤집는다
     (R-36은 그 스위치를 만들고 증명하는 리프이지, 오늘 당장 켜는
     리프가 아니다).
   mandate가 있으면(있다고 주장하면), 먼저 `context.mandate_revision_id`가
   지금 tenant의 active revision과 같은지부터 대조한다(task-1806, fence와
   같은 관측-vs-현재 패턴) — amendment로 revision이 바뀌었는데 호출부가
   여전히 옛 revision id를 들고 있으면 `RISK_MANDATE_REVISION_STALE` DENY.
   일치해야 비로소 `mandates.evaluate_policy()`로 정식 평가한다.

task-1717 P0-D — 전수감사가 지적한 결함은 "이 함수가 내리는 실제 결정이
`risk_decision` WORM 테이블에 전혀 기록되지 않아 `GateDecision.decision_id`가
항상 None이고, 그래서 `orders.risk_decision_id`를 쓰는 운영 경로가 0개"였다.
위 2단 게이트 로직 자체(fence/control/mandate)는 그대로 두고, 그 결과를
`_record_decision()`으로 WORM에 기록해 `decision_id`를 채운다 — 이 결정은
`GateKind.PRE_SUBMIT`이지만 `evaluate_pre_submit()`이 쓰는 4-rule 스키마와는
별개(그 함수를 호출하지 않으므로 CB/distrust/connection 필드가 없다)다.
`fenced_submit.bind_to_worm_decision`/`decision_binding.verify_decision_binding`이
요구하는 최소 계약(top-level `symbol`/`side`/`quantity`/`fence_snapshot`,
`execution_ref`)만 만족시킨다. mandate를 정식 평가한 경우
`GateDecision.policy_decision_id`에 그 `PolicyDecisionView.id`도 함께
반환한다(신규 필드, mandates bounded context 자체 결정 id — risk_gate WORM
과 별개 테이블).
"""
from __future__ import annotations

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
    recorder: RiskDecisionRecorder,
    *,
    context: OrderContext,
    outcome: GateOutcome,
    reason_codes: tuple[str, ...],
    fence: Mapping[str, int],
) -> UUID:
    now = datetime.now(timezone.utc)
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
        latency_us=1,
    )
    await recorder.record(decision, inputs, actor="order_service.foundation_gate")
    return decision_id


def make_foundation_pre_submit_gate(pool: asyncpg.Pool, *, require_mandate: bool) -> PreSubmitGate:
    risk_repo = PostgresRiskGateRepository(pool)
    mandate_repo = PostgresMandateRepository(pool)
    recorder = RiskDecisionRecorder(pool, PostgresDecisionRepository(pool), InProcessEventBus())

    async def gate(context: OrderContext) -> GateDecision:
        pairs = fence_pairs_for(context.user_id, context.exchange, f"exec:{context.execution_id}")
        fence_snapshot, active_controls = await risk_repo.read_fence_and_controls(pairs)
        fence = _flatten_fence(fence_snapshot)

        if context.observed_fence is not None and _is_stale(context.observed_fence, fence):
            decision_id = await _record_decision(
                recorder, context=context, outcome=GateOutcome.DENY,
                reason_codes=("RISK_FENCE_STALE",), fence=fence,
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
                reason_codes=reason_codes, fence=fence,
            )
            return GateDecision(
                outcome=GateOutcome.DENY, reason_codes=reason_codes,
                fence_snapshot=fence, decision_id=decision_id,
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
                    reason_codes=("RISK_MANDATE_REQUIRED",), fence=fence,
                )
                return GateDecision(
                    outcome=GateOutcome.DENY, reason_codes=("RISK_MANDATE_REQUIRED",),
                    fence_snapshot=fence, decision_id=decision_id,
                )
            decision_id = await _record_decision(
                recorder, context=context, outcome=GateOutcome.ALLOW,
                reason_codes=(), fence=fence,
            )
            return GateDecision(
                outcome=GateOutcome.ALLOW, fence_snapshot=fence, decision_id=decision_id,
            )

        # task-1806 — R-36과 같은 관측-vs-현재 패턴을 mandate revision에도
        # 적용한다: `context.mandate_revision_id`는 이 실행이 마지막으로
        # 바인딩된(관측한) revision이다. 그 사이 amendment로 새 revision이
        # activate되면(이전 revision은 SUPERSEDED) `evaluate_mandate_policy`는
        # 항상 "현재" active revision을 기준으로 평가하므로, 대조 없이 그냥
        # 통과시키면 실행이 자신이 동의한 적 없는(더 느슨하거나 더 엄격한)
        # 규칙으로 조용히 재평가된다 — fence stale과 동일한 클래스의 결함.
        # 불일치면 재바인딩을 요구하며 거부한다.
        mandate = await mandate_repo.get_mandate(context.user_id)
        if mandate is None or mandate.active_revision_id != context.mandate_revision_id:
            decision_id = await _record_decision(
                recorder, context=context, outcome=GateOutcome.DENY,
                reason_codes=("RISK_MANDATE_REVISION_STALE",), fence=fence,
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
                reason_codes=("RISK_INPUT_MANDATE_MISSING",), fence=fence,
            )
            return GateDecision(
                outcome=GateOutcome.DENY, reason_codes=("RISK_INPUT_MANDATE_MISSING",),
                fence_snapshot=fence, decision_id=decision_id,
            )

        if mandate_decision.outcome != MandateOutcome.ALLOW:
            reason_codes = tuple(mandate_decision.reason_codes)
            decision_id = await _record_decision(
                recorder, context=context, outcome=GateOutcome.DENY,
                reason_codes=reason_codes, fence=fence,
            )
            return GateDecision(
                outcome=GateOutcome.DENY, reason_codes=reason_codes, fence_snapshot=fence,
                decision_id=decision_id, policy_decision_id=mandate_decision.id,
            )
        decision_id = await _record_decision(
            recorder, context=context, outcome=GateOutcome.ALLOW, reason_codes=(), fence=fence,
        )
        return GateDecision(
            outcome=GateOutcome.ALLOW, fence_snapshot=fence, decision_id=decision_id,
            policy_decision_id=mandate_decision.id,
        )

    return gate
