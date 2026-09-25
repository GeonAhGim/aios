"""`foundation_gate_decision.py` -- `is_stale`/`flatten_fence`/`record_decision`
coverage. No unit test exists for these three units anywhere in the repo
(grepped for `is_stale`/`flatten_fence`/`record_decision` in `tests/` before
writing this file).

`record_decision` only performs I/O through its injected `recorder.record(...)`
call -- the fake recorder below is a plain duck-typed stand-in, never a real
DB pool, so `cast(Any, ...)` bridges it past the concrete `RiskDecisionRecorder`
type hint.
"""

from __future__ import annotations

import time
from typing import Any, cast
from uuid import uuid4

from src.core.risk.decision import RiskDecision, RiskOutcome
from src.foundation.risk_gate.domain.models import FenceSnapshot, SafetyScope
from src.services.order_service.foundation_gate_decision import (
    flatten_fence,
    is_stale,
    record_decision,
)
from src.services.order_service.gate import GateOutcome, OrderContext
from src.services.risk_decision_recorder import RiskDecisionRecorder


def test_is_stale_true_when_current_token_increased() -> None:
    assert is_stale({"a": 5}, {"a": 6}) is True


def test_is_stale_false_when_tokens_equal() -> None:
    assert is_stale({"a": 5}, {"a": 5}) is False


def test_is_stale_false_when_pair_absent_from_current() -> None:
    assert is_stale({"a": 5}, {}) is False


def test_flatten_fence_formats_scope_colon_ref_keys() -> None:
    snapshot = FenceSnapshot(
        tokens={
            (SafetyScope.TENANT, "t1"): 3,
            (SafetyScope.PROVIDER, "bitget"): 7,
        }
    )

    assert flatten_fence(snapshot) == {"TENANT:t1": 3, "PROVIDER:bitget": 7}


class _FakeRecorder:
    def __init__(self) -> None:
        self.decision: RiskDecision | None = None

    async def record(self, decision: RiskDecision, inputs: Any, *, actor: str) -> None:
        self.decision = decision


async def test_record_decision_maps_outcome_and_computes_latency_floor() -> None:
    fake_recorder = _FakeRecorder()
    context = OrderContext(
        user_id=uuid4(),
        execution_id=1,
        exchange="bitget",
        mandate_revision_id=None,
    )

    await record_decision(
        cast(RiskDecisionRecorder, fake_recorder),
        context=context,
        outcome=GateOutcome.DENY,
        reason_codes=("SOME_REASON",),
        fence={"TENANT:t1": 1},
        start_ns=time.perf_counter_ns(),
    )

    assert fake_recorder.decision is not None
    assert fake_recorder.decision.outcome == RiskOutcome.DENY
    assert isinstance(fake_recorder.decision.latency_us, int)
    assert fake_recorder.decision.latency_us >= 1
