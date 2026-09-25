"""task-7468 -- `foundation_gate_mandate_layer.evaluate_mandate_layer` coverage.

This function has zero prior unit tests anywhere in the repo. It implements a
fail-closed mandate-revision-staleness gate (task-1806, same observed-vs-current
pattern as the R-36 fence-staleness precedent): if `context.mandate_revision_id`
does not match the tenant's currently active mandate revision, it must DENY
with `RISK_MANDATE_REVISION_STALE` rather than silently re-evaluating the
execution against rules it never agreed to. Fakes only -- no DB, no network.

Covers all 5 branches of the function body:
1. mandate is None -> DENY RISK_MANDATE_REVISION_STALE
2. mandate.active_revision_id mismatch -> DENY RISK_MANDATE_REVISION_STALE
3. evaluate_mandate_policy raises NoActiveMandateError -> DENY RISK_INPUT_MANDATE_MISSING
4. evaluate_mandate_policy returns non-ALLOW -> DENY forwarding its reason_codes
5. evaluate_mandate_policy returns ALLOW -> delegates to finish_allow(policy_decision_id=...)
"""

from __future__ import annotations

from typing import Any, cast
from uuid import UUID, uuid4

import pytest

from src.foundation.mandates.application.evaluate_policy import NoActiveMandateError
from src.foundation.mandates.contracts.v1 import PolicyOutcome as PolicyOutcome
from src.foundation.mandates.ports.repository import MandateRepository
from src.services.order_service import foundation_gate_mandate_layer as target
from src.services.order_service.foundation_gate_mandate_layer import evaluate_mandate_layer
from src.services.order_service.gate import GateDecision, GateOutcome, OrderContext
from src.services.risk_decision_recorder import RiskDecisionRecorder

_TENANT_ID: UUID = uuid4()
_CURRENT_REVISION_ID: UUID = uuid4()
_FENCE: dict[str, int] = {"scope:ref": 1}
_START_NS = 0


class _FakeMandate:
    def __init__(self, active_revision_id: UUID) -> None:
        self.active_revision_id = active_revision_id


class _FakeMandateRepo:
    def __init__(self, mandate: object | None) -> None:
        self._mandate = mandate
        self.calls: list[tuple[UUID, UUID | None]] = []

    async def get_mandate(self, tenant_id: UUID, portfolio_id: UUID | None = None) -> Any:
        self.calls.append((tenant_id, portfolio_id))
        return self._mandate


class _FakeRecorder:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    async def record(self, decision: Any, inputs: Any, *, actor: str) -> None:
        self.records.append({"decision": decision, "inputs": inputs, "actor": actor})


class _FakeFinishAllow:
    def __init__(self, result: GateDecision) -> None:
        self._result = result
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, **kwargs: Any) -> GateDecision:
        self.calls.append(kwargs)
        return self._result


class _FakePolicyDecision:
    def __init__(self, *, outcome: PolicyOutcome, reason_codes: tuple[str, ...], id: UUID) -> None:
        self.outcome = outcome
        self.reason_codes = reason_codes
        self.id = id


def _make_context(mandate_revision_id: UUID | None) -> OrderContext:
    return OrderContext(
        user_id=_TENANT_ID,
        execution_id=1,
        exchange="bitget",
        mandate_revision_id=mandate_revision_id,
    )


async def _evaluate(
    *,
    mandate_repo: _FakeMandateRepo,
    recorder: _FakeRecorder,
    context: OrderContext,
    finish_allow: _FakeFinishAllow,
) -> GateDecision:
    # Fakes are structurally complete for the members `evaluate_mandate_layer`
    # actually calls; cast tells mypy to trust that (the ports are large
    # Protocols and only a couple of methods are exercised per test).
    return await evaluate_mandate_layer(
        mandate_repo=cast(MandateRepository, mandate_repo),
        recorder=cast(RiskDecisionRecorder, recorder),
        context=context,
        fence=_FENCE,
        start_ns=_START_NS,
        finish_allow=finish_allow,
    )


@pytest.mark.asyncio
async def test_mandate_none_denies_revision_stale() -> None:
    mandate_repo = _FakeMandateRepo(None)
    recorder = _FakeRecorder()
    finish_allow = _FakeFinishAllow(GateDecision(outcome=GateOutcome.ALLOW))
    context = _make_context(_CURRENT_REVISION_ID)

    result = await _evaluate(
        mandate_repo=mandate_repo,
        recorder=recorder,
        context=context,
        finish_allow=finish_allow,
    )

    assert result.outcome == GateOutcome.DENY
    assert result.reason_codes == ("RISK_MANDATE_REVISION_STALE",)
    assert result.decision_id is not None
    assert finish_allow.calls == []


@pytest.mark.asyncio
async def test_mandate_revision_mismatch_denies_revision_stale() -> None:
    stale_revision_id = uuid4()
    mandate_repo = _FakeMandateRepo(_FakeMandate(active_revision_id=_CURRENT_REVISION_ID))
    recorder = _FakeRecorder()
    finish_allow = _FakeFinishAllow(GateDecision(outcome=GateOutcome.ALLOW))
    context = _make_context(stale_revision_id)

    result = await _evaluate(
        mandate_repo=mandate_repo,
        recorder=recorder,
        context=context,
        finish_allow=finish_allow,
    )

    assert result.outcome == GateOutcome.DENY
    assert result.reason_codes == ("RISK_MANDATE_REVISION_STALE",)
    assert result.decision_id is not None
    assert finish_allow.calls == []


@pytest.mark.asyncio
async def test_no_active_mandate_error_denies_input_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _raise_no_active_mandate(*args: Any, **kwargs: Any) -> Any:
        raise NoActiveMandateError(str(_TENANT_ID))

    monkeypatch.setattr(target, "evaluate_mandate_policy", _raise_no_active_mandate)

    mandate_repo = _FakeMandateRepo(_FakeMandate(active_revision_id=_CURRENT_REVISION_ID))
    recorder = _FakeRecorder()
    finish_allow = _FakeFinishAllow(GateDecision(outcome=GateOutcome.ALLOW))
    context = _make_context(_CURRENT_REVISION_ID)

    result = await _evaluate(
        mandate_repo=mandate_repo,
        recorder=recorder,
        context=context,
        finish_allow=finish_allow,
    )

    assert result.outcome == GateOutcome.DENY
    assert result.reason_codes == ("RISK_INPUT_MANDATE_MISSING",)
    assert finish_allow.calls == []


@pytest.mark.asyncio
async def test_policy_deny_forwards_reason_codes(monkeypatch: pytest.MonkeyPatch) -> None:
    policy_decision_id = uuid4()
    fake_decision = _FakePolicyDecision(
        outcome=PolicyOutcome.DENY,
        reason_codes=("SOME_POLICY_REASON",),
        id=policy_decision_id,
    )

    async def _fake_evaluate(*args: Any, **kwargs: Any) -> Any:
        return fake_decision

    monkeypatch.setattr(target, "evaluate_mandate_policy", _fake_evaluate)

    mandate_repo = _FakeMandateRepo(_FakeMandate(active_revision_id=_CURRENT_REVISION_ID))
    recorder = _FakeRecorder()
    finish_allow = _FakeFinishAllow(GateDecision(outcome=GateOutcome.ALLOW))
    context = _make_context(_CURRENT_REVISION_ID)

    result = await _evaluate(
        mandate_repo=mandate_repo,
        recorder=recorder,
        context=context,
        finish_allow=finish_allow,
    )

    assert result.outcome == GateOutcome.DENY
    assert result.reason_codes == ("SOME_POLICY_REASON",)
    assert result.policy_decision_id == policy_decision_id
    assert finish_allow.calls == []


@pytest.mark.asyncio
async def test_policy_allow_calls_finish_allow_with_policy_decision_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy_decision_id = uuid4()
    fake_decision = _FakePolicyDecision(
        outcome=PolicyOutcome.ALLOW,
        reason_codes=(),
        id=policy_decision_id,
    )

    async def _fake_evaluate(*args: Any, **kwargs: Any) -> Any:
        return fake_decision

    monkeypatch.setattr(target, "evaluate_mandate_policy", _fake_evaluate)

    mandate_repo = _FakeMandateRepo(_FakeMandate(active_revision_id=_CURRENT_REVISION_ID))
    recorder = _FakeRecorder()
    sentinel = GateDecision(outcome=GateOutcome.ALLOW, policy_decision_id=policy_decision_id)
    finish_allow = _FakeFinishAllow(sentinel)
    context = _make_context(_CURRENT_REVISION_ID)

    result = await _evaluate(
        mandate_repo=mandate_repo,
        recorder=recorder,
        context=context,
        finish_allow=finish_allow,
    )

    assert len(finish_allow.calls) == 1
    assert finish_allow.calls[0] == {"policy_decision_id": policy_decision_id}
    assert result is sentinel
