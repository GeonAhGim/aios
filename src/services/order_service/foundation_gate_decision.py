"""`foundation_gate.py`의 WORM `risk_decision` 기록 헬퍼 — task-1717 P0-D
`_record_decision()`을 책임 분리(task-4006, P6 LOC 분할, 로직 이동만·불변식
불변). fence 평탄화(`flatten_fence`)/stale 판정(`is_stale`)도 같은 "결정
기록에 필요한 입력 가공" 축이라 함께 옮긴다 — 게이트 4단 평가 순서 자체는
`foundation_gate.py`에 남는다.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel

from src.core.risk.decision import GateKind, RiskDecision, RiskOutcome
from src.core.risk.hashing import canonical_json, sha256_hex
from src.foundation.risk_gate.domain.models import FenceSnapshot
from src.services.order_service.gate import GateOutcome, OrderContext
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
    게이트는 CB/distrust/connection을 보지 않는다, `foundation_gate.py`
    모듈 docstring)."""

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


def flatten_fence(snapshot: FenceSnapshot) -> dict[str, int]:
    return {f"{scope.value}:{ref}": token for (scope, ref), token in snapshot.tokens.items()}


def is_stale(observed: Mapping[str, int], current: Mapping[str, int]) -> bool:
    """§3.6 stale 정의 — 토큰 증가만 stale로 본다. `observed`에만 있고
    `current`에 없는 pair는 없다(같은 `fence_pairs_for` 5쌍을 항상 읽는다)."""
    return any(current.get(pair, 0) > observed_token for pair, observed_token in observed.items())


async def record_decision(
    recorder: RiskDecisionRecorder,
    *,
    context: OrderContext,
    outcome: GateOutcome,
    reason_codes: tuple[str, ...],
    fence: Mapping[str, int],
    start_ns: int,
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
