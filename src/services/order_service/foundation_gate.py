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
   mandate가 있으면 두 역할 모두 `mandates.evaluate_policy()`로 정식
   평가한다.
"""
from __future__ import annotations

from collections.abc import Mapping

import asyncpg

from src.core.logging.audit_log import record_audit_log
from src.foundation.mandates.adapters.postgres_repository import PostgresMandateRepository
from src.foundation.mandates.application.evaluate_policy import NoActiveMandateError
from src.foundation.mandates.application.evaluate_policy import evaluate as evaluate_mandate_policy
from src.foundation.mandates.contracts.v1 import PolicyEvaluationSubject
from src.foundation.mandates.contracts.v1 import PolicyOutcome as MandateOutcome
from src.foundation.risk_gate.adapters.postgres_repository import PostgresRiskGateRepository
from src.foundation.risk_gate.domain.fence import fence_pairs_for
from src.foundation.risk_gate.domain.models import FenceSnapshot
from src.services.order_service.gate import GateDecision, GateOutcome, OrderContext, PreSubmitGate


def _flatten_fence(snapshot: FenceSnapshot) -> dict[str, int]:
    return {f"{scope.value}:{ref}": token for (scope, ref), token in snapshot.tokens.items()}


def _is_stale(observed: Mapping[str, int], current: Mapping[str, int]) -> bool:
    """§3.6 stale 정의 — 토큰 증가만 stale로 본다. `observed`에만 있고
    `current`에 없는 pair는 없다(같은 `fence_pairs_for` 5쌍을 항상 읽는다)."""
    return any(current.get(pair, 0) > observed_token for pair, observed_token in observed.items())


def make_foundation_pre_submit_gate(pool: asyncpg.Pool, *, require_mandate: bool) -> PreSubmitGate:
    risk_repo = PostgresRiskGateRepository(pool)
    mandate_repo = PostgresMandateRepository(pool)

    async def gate(context: OrderContext) -> GateDecision:
        pairs = fence_pairs_for(context.user_id, context.exchange, f"exec:{context.execution_id}")
        fence_snapshot, active_controls = await risk_repo.read_fence_and_controls(pairs)
        fence = _flatten_fence(fence_snapshot)

        if context.observed_fence is not None and _is_stale(context.observed_fence, fence):
            return GateDecision(
                outcome=GateOutcome.DENY, reason_codes=("RISK_FENCE_STALE",), fence_snapshot=fence
            )

        if active_controls:
            return GateDecision(
                outcome=GateOutcome.DENY,
                reason_codes=tuple(
                    f"RISK_KILL_SWITCH_ACTIVE_{c.scope.value}" for c in active_controls
                ),
                fence_snapshot=fence,
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
                return GateDecision(
                    outcome=GateOutcome.DENY,
                    reason_codes=("RISK_MANDATE_REQUIRED",),
                    fence_snapshot=fence,
                )
            return GateDecision(outcome=GateOutcome.ALLOW, fence_snapshot=fence)

        try:
            decision = await evaluate_mandate_policy(
                mandate_repo,
                tenant_id=context.user_id,
                subject=PolicyEvaluationSubject(command_type="LEGACY_ORDER_SUBMIT"),
            )
        except NoActiveMandateError:
            return GateDecision(
                outcome=GateOutcome.DENY,
                reason_codes=("RISK_INPUT_MANDATE_MISSING",),
                fence_snapshot=fence,
            )

        if decision.outcome != MandateOutcome.ALLOW:
            return GateDecision(
                outcome=GateOutcome.DENY,
                reason_codes=tuple(decision.reason_codes),
                fence_snapshot=fence,
            )
        return GateDecision(outcome=GateOutcome.ALLOW, fence_snapshot=fence)

    return gate
