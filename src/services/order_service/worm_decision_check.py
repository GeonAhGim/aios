"""§3.6 3' — WORM `risk_decision` 재조회·결속 대조의 I/O 어댑터(task-1532).

순수 규칙은 `decision_binding.verify_decision_binding`(I/O 없음)에 있고, 이
모듈은 그 앞뒤의 I/O만 맡는다: tenant 스코프 조회(`DecisionReader`) →
불일치면 감사 `risk_decision_integrity_rejected` 기록 → `RiskDecisionIntegrity
Error`. `fenced_submit.submit_with_fence`가 claim(orders INSERT) *전에*
호출하므로 거부 시 orders 행도 어댑터 호출도 없다.

`fenced_submit.py`와 같은 이유로 foundation을 import하지 않는다 —
`DecisionReader`는 `PostgresDecisionRepository.get_for_tenant`와 같은
시그니처의 구조적 Protocol이고 조립부가 주입한다.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol
from uuid import UUID

import asyncpg

from src.core.logging.audit_log import record_audit_log
from src.core.risk.decision import RiskDecision
from src.data.models.trading import Order
from src.services.order_service.decision_binding import (
    MISMATCH_DECISION_MISSING,
    REASON_INTEGRITY_MISMATCH,
    verify_decision_binding,
)
from src.services.order_service.gate import GateDecision

AUDIT_DECISION_INTEGRITY_REJECTED = "risk_decision_integrity_rejected"


class RiskDecisionIntegrityError(Exception):
    """I4·I10 — WORM `risk_decision` 행이 이 tenant에 없거나, execution_ref·
    intent·F0가 주문·호출자 값과 다르다. claim 전에 거부됐고 거래소는
    호출되지 않았다. reason은 §3.4 `INTEGRITY_RISK_FINGERPRINT_MISMATCH`."""

    reason_code = REASON_INTEGRITY_MISMATCH

    def __init__(self, mismatches: tuple[str, ...], *, decision_id: UUID) -> None:
        self.mismatches = mismatches
        self.decision_id = decision_id
        super().__init__(
            f"{REASON_INTEGRITY_MISMATCH}: risk_decision {decision_id} does not bind to "
            f"this order ({', '.join(mismatches)})"
        )


class DecisionReader(Protocol):
    """`PostgresDecisionRepository`가 구조적으로 만족한다(foundation 포트
    `DecisionRepository.get_for_tenant`와 같은 시그니처)."""

    async def get_for_tenant(
        self, decision_id: UUID, tenant_id: UUID
    ) -> tuple[RiskDecision, Mapping[str, Any]] | None: ...


async def bind_to_worm_decision(
    pool: asyncpg.Pool,
    order: Order,
    *,
    user_id: UUID,
    actor: str,
    gate_decision: GateDecision,
    decision_reader: DecisionReader,
    trace_id: UUID | None,
) -> Mapping[str, int]:
    """WORM 행을 tenant 스코프로 재조회해 결속을 대조하고, 통과하면 WORM의
    F0를 돌려준다. 불일치면 감사 후 `RiskDecisionIntegrityError`. 감사에는
    불일치 *필드 이름*만 남긴다(§7 — inputs_snapshot 전문·주문 원값 금지)."""
    decision_id = gate_decision.decision_id
    if decision_id is None:  # 호출자가 먼저 걸러야 하지만 여기서도 fail-closed
        raise RiskDecisionIntegrityError((MISMATCH_DECISION_MISSING,), decision_id=UUID(int=0))
    recorded = await decision_reader.get_for_tenant(decision_id, user_id)
    if recorded is None:
        mismatches: tuple[str, ...] = (MISMATCH_DECISION_MISSING,)
    else:
        decision, inputs_snapshot = recorded
        binding = verify_decision_binding(
            order,
            caller_fence=gate_decision.fence_snapshot,
            recorded=decision,
            inputs_snapshot=inputs_snapshot,
        )
        if binding.ok:
            return binding.fence_snapshot
        mismatches = binding.mismatches

    async with pool.acquire() as conn:
        await record_audit_log(
            conn,
            actor_agent=actor,
            action_type=AUDIT_DECISION_INTEGRITY_REJECTED,
            user_id=user_id,
            target_type="risk_decision",
            target_id=str(decision_id),
            decision_data={
                "reason_code": REASON_INTEGRITY_MISMATCH,
                "mismatches": list(mismatches),
                "client_order_id": order.client_order_id,
                "execution_id": order.execution_id,
            },
            trace_id=trace_id,
        )
    raise RiskDecisionIntegrityError(mismatches, decision_id=decision_id)


__all__ = [
    "AUDIT_DECISION_INTEGRITY_REJECTED",
    "DecisionReader",
    "RiskDecisionIntegrityError",
    "bind_to_worm_decision",
]
