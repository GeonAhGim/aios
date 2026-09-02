"""`submit_order(pre_submit_gate=...)`의 실제 구현체 — foundation risk_gate/
mandates를 여기서만 import한다(`gate.py`/`submit.py`는 foundation을 모른다,
PM 지침).

전수감사 §6 — "foundation은 실행 경로를 게이트하지 않는 병렬 섬"이었다.
2계층으로 나눠 legacy 실행을 깨지 않으면서 kill switch만 먼저 배선한다:

1층(항상 검사): GLOBAL/TENANT/ACCOUNT/이 거래소의 PROVIDER 범위에 활성
   safety control이 하나라도 있으면 mandate 유무와 무관하게 DENY. 이게
   kill switch를 legacy 경로에 처음으로 연결하는 지점이다.
2층(mandate_revision_id가 있을 때만): mandates.evaluate_policy()로 정식
   평가. 없으면(기존 실행 전부가 여기 해당 — strategy_executions에 아직
   아무도 이 컬럼을 채우지 않음) DENY하지 않고 audit_log만 남긴 뒤
   통과시킨다 — RSK-002("입력 없으면 DENY")를 지금 당장 legacy 전체에
   소급 적용하면 기존 PAPER 실행 전부가 멈추는 회귀이기 때문이다.
   `AIOS_REQUIRE_MANDATE_FOR_SUBMIT=1`이면 이 예외를 끄고 완전히
   RSK-002를 만족시킨다 — 실행 생성 UI가 mandate를 연결하기 시작하면
   그때 기본값을 뒤집는다.
"""
from __future__ import annotations

import os

import asyncpg

from src.core.logging.audit_log import record_audit_log
from src.foundation.mandates.adapters.postgres_repository import PostgresMandateRepository
from src.foundation.mandates.application.evaluate_policy import NoActiveMandateError
from src.foundation.mandates.application.evaluate_policy import evaluate as evaluate_mandate_policy
from src.foundation.mandates.contracts.v1 import PolicyEvaluationSubject
from src.foundation.mandates.contracts.v1 import PolicyOutcome as MandateOutcome
from src.foundation.risk_gate.adapters.postgres_repository import PostgresRiskGateRepository
from src.foundation.risk_gate.domain.models import SafetyControlState
from src.services.order_service.gate import GateDecision, GateOutcome, OrderContext, PreSubmitGate

REQUIRE_MANDATE_ENV_VAR = "AIOS_REQUIRE_MANDATE_FOR_SUBMIT"


def _mandate_required() -> bool:
    return os.environ.get(REQUIRE_MANDATE_ENV_VAR, "0") == "1"


def make_foundation_pre_submit_gate(pool: asyncpg.Pool) -> PreSubmitGate:
    risk_repo = PostgresRiskGateRepository(pool)
    mandate_repo = PostgresMandateRepository(pool)

    async def gate(context: OrderContext) -> GateDecision:
        controls = await risk_repo.list_active_controls(
            tenant_id=context.user_id, provider_code=context.exchange
        )
        active = [c for c in controls if c.state == SafetyControlState.ACTIVE]
        if active:
            return GateDecision(
                outcome=GateOutcome.DENY,
                reason_codes=tuple(
                    f"RISK_KILL_SWITCH_ACTIVE_{c.scope.value}" for c in active
                ),
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
            if _mandate_required():
                return GateDecision(
                    outcome=GateOutcome.DENY, reason_codes=("RISK_MANDATE_REQUIRED",)
                )
            return GateDecision(outcome=GateOutcome.ALLOW)

        try:
            decision = await evaluate_mandate_policy(
                mandate_repo,
                tenant_id=context.user_id,
                subject=PolicyEvaluationSubject(command_type="LEGACY_ORDER_SUBMIT"),
            )
        except NoActiveMandateError:
            return GateDecision(
                outcome=GateOutcome.DENY, reason_codes=("RISK_INPUT_MANDATE_MISSING",)
            )

        if decision.outcome != MandateOutcome.ALLOW:
            return GateDecision(
                outcome=GateOutcome.DENY, reason_codes=tuple(decision.reason_codes)
            )
        return GateDecision(outcome=GateOutcome.ALLOW)

    return gate
