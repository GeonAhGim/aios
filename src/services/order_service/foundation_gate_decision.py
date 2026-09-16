"""WORM `risk_decision` recording helpers for `foundation_gate.py` --
task-1717 P0-D's `_record_decision()` split out for LOC (task-4006, P6,
logic move only, invariants unchanged). Fence flattening (`flatten_fence`)
and staleness detection (`is_stale`) move alongside it -- same "inputs for
recording a decision" axis. The 4-layer gate evaluation order itself stays
in `foundation_gate.py`.
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
    """For the WORM `inputs_snapshot` -- fills only the minimal contract
    `decision_binding.verify_decision_binding` requires (top-level
    symbol/side/quantity/fence_snapshot). Schema differs from
    `evaluate_pre_submit._PreSubmitInputs` (this gate does not look at
    CB/distrust/connection, see `foundation_gate.py`'s module docstring)."""

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
