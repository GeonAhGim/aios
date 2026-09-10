"""L4_compliance_and_regulatory_v1.0.md#9 CM-13 -- `application/explain.py`
unit tests.

task-2510 DoD mapping: (a) same `decision_id` -> byte-identical output across
two calls, (b) stored bundle rule_hash vs. today's recompiled hash mismatch
-> rejected with a defined CM error code, never silently re-evaluated,
(c) unknown `decision_id` -> a defined `ExplainError`, not a raw
exception, (d) no reimplementation of `evaluate_policy()`/mapping -- this
suite uses a fake `MandateRepository` and never calls the rule engine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from src.foundation.mandates.application.explain import (
    ExplainError,
    ExplainErrorCode,
    explain,
)
from src.foundation.mandates.contracts.v1 import ComplianceVerdict
from src.foundation.mandates.domain.models import (
    Autonomy,
    MandateRevision,
    MandateRevisionState,
    PolicyBundle,
    PolicyDecision,
    PolicyOutcome,
)
from src.foundation.mandates.domain.rules import compile_rule_hash

_NOW = datetime(2026, 1, 5, 5, 0, 0, tzinfo=timezone.utc)


def _revision(**overrides: object) -> MandateRevision:
    base: dict[str, Any] = {
        "id": uuid4(),
        "mandate_id": uuid4(),
        "revision_no": 1,
        "state": MandateRevisionState.ACTIVE,
        "max_total_exposure_pct": 80.0,
        "max_single_instrument_pct": 20.0,
        "min_cash_buffer_pct": 5.0,
        "max_daily_loss_pct": 3.0,
        "allowed_autonomy": Autonomy.PAPER,
        "forbidden_assets": (),
    }
    base.update(overrides)
    return MandateRevision(**base)


def _bundle(revision: MandateRevision, *, rule_hash: str | None = None) -> PolicyBundle:
    return PolicyBundle(
        id=uuid4(),
        mandate_revision_id=revision.id,
        compiler_version="v1",
        rule_hash=rule_hash if rule_hash is not None else compile_rule_hash(revision),
        created_at=_NOW,
    )


def _decision(
    bundle: PolicyBundle,
    *,
    outcome: PolicyOutcome = PolicyOutcome.DENY,
    reason_codes: tuple[str, ...] = ("POLICY_MAX_TOTAL_EXPOSURE",),
) -> PolicyDecision:
    return PolicyDecision(
        id=uuid4(),
        tenant_id=uuid4(),
        bundle_id=bundle.id,
        command_type="submit_order",
        command_fingerprint="a" * 64,
        outcome=outcome,
        reason_codes=reason_codes,
        obligations=("REQUIRE_RISK_GATE",),
        evaluated_at=_NOW,
        expires_at=None,
    )


@dataclass
class _FakeRepo:
    """explain()이 실제로 쓰는 세 메서드만 흉내낸다 -- `MandateRepository`
    전체를 구현하지 않아도 구조적 타이핑으로 충분하다."""

    decisions: dict[UUID, PolicyDecision] = field(default_factory=dict)
    bundles: dict[UUID, PolicyBundle] = field(default_factory=dict)
    revisions: dict[UUID, MandateRevision] = field(default_factory=dict)

    async def get_policy_decision(self, decision_id: UUID) -> PolicyDecision | None:
        return self.decisions.get(decision_id)

    async def get_bundle(self, bundle_id: UUID) -> PolicyBundle | None:
        return self.bundles.get(bundle_id)

    async def get_revision(self, revision_id: UUID) -> MandateRevision | None:
        return self.revisions.get(revision_id)


def _repo_with(
    revision: MandateRevision, bundle: PolicyBundle, decision: PolicyDecision
) -> _FakeRepo:
    return _FakeRepo(
        decisions={decision.id: decision},
        bundles={bundle.id: bundle},
        revisions={revision.id: revision},
    )


async def test_explain_maps_stored_decision_to_compliance_decision() -> None:
    revision = _revision()
    bundle = _bundle(revision)
    decision = _decision(bundle, outcome=PolicyOutcome.DENY)
    repo = _repo_with(revision, bundle, decision)

    result = await explain(repo, decision.id)

    assert result.decision_id == decision.id
    assert result.verdict == ComplianceVerdict.DENY
    assert [hit.rule_id for hit in result.rule_hits] == list(decision.reason_codes)
    assert result.inputs_hash == decision.command_fingerprint
    assert result.bundle_version == bundle.rule_hash
    assert result.evaluated_at == decision.evaluated_at


async def test_explain_allow_outcome_maps_to_allow_verdict_with_no_hits() -> None:
    revision = _revision()
    bundle = _bundle(revision)
    decision = _decision(bundle, outcome=PolicyOutcome.ALLOW, reason_codes=())
    repo = _repo_with(revision, bundle, decision)

    result = await explain(repo, decision.id)

    assert result.verdict == ComplianceVerdict.ALLOW
    assert result.rule_hits == []


async def test_explain_is_byte_identical_across_repeated_calls() -> None:
    """DoD (a) -- same decision_id, called twice, produces identical JSON
    bytes (dict order + every field value included)."""
    revision = _revision()
    bundle = _bundle(revision)
    decision = _decision(bundle)
    repo = _repo_with(revision, bundle, decision)

    first = await explain(repo, decision.id)
    second = await explain(repo, decision.id)

    assert first.model_dump_json() == second.model_dump_json()
    assert first == second


async def test_explain_rejects_when_stored_bundle_hash_has_drifted() -> None:
    """DoD (b) -- stored `rule_hash` no longer matches what the current
    compiler produces for the same revision. explain() must reject with a
    CM error code, not quietly recompute a fresh decision."""
    revision = _revision()
    drifted_bundle = _bundle(revision, rule_hash="f" * 64)
    decision = _decision(drifted_bundle)
    repo = _repo_with(revision, drifted_bundle, decision)

    with pytest.raises(ExplainError) as excinfo:
        await explain(repo, decision.id)

    assert excinfo.value.code == ExplainErrorCode.BUNDLE_DRIFTED


async def test_explain_rejects_unknown_decision_id_with_defined_error() -> None:
    """DoD (c) -- a decision_id that was never stored is rejected with the
    defined `ExplainError`/`DECISION_NOT_FOUND`, not a raw exception such as
    `AttributeError`/`KeyError`."""
    repo = _FakeRepo()

    with pytest.raises(ExplainError) as excinfo:
        await explain(repo, uuid4())

    assert excinfo.value.code == ExplainErrorCode.DECISION_NOT_FOUND


async def test_explain_rejects_when_referenced_bundle_is_missing() -> None:
    """FK integrity edge case: policy_decision.bundle_id points at a row
    that (should never but) does not exist -- still fails closed instead of
    raising AttributeError on `bundle.rule_hash`."""
    revision = _revision()
    bundle = _bundle(revision)
    decision = _decision(bundle)
    repo = _FakeRepo(decisions={decision.id: decision})  # bundle intentionally absent

    with pytest.raises(ExplainError) as excinfo:
        await explain(repo, decision.id)

    assert excinfo.value.code == ExplainErrorCode.BUNDLE_DRIFTED


async def test_explain_rejects_when_referenced_revision_is_missing() -> None:
    revision = _revision()
    bundle = _bundle(revision)
    decision = _decision(bundle)
    repo = _FakeRepo(decisions={decision.id: decision}, bundles={bundle.id: bundle})

    with pytest.raises(ExplainError) as excinfo:
        await explain(repo, decision.id)

    assert excinfo.value.code == ExplainErrorCode.BUNDLE_DRIFTED


async def test_explain_never_mutates_or_reinserts_anything() -> None:
    """DoD (d) -- explain() only reads; a repo that cannot write must still
    work (proves no re-evaluation/insert path is exercised)."""
    revision = _revision()
    bundle = _bundle(revision)
    decision = _decision(bundle)
    repo = _repo_with(revision, bundle, decision)
    assert not hasattr(repo, "insert_policy_decision")

    result = await explain(repo, decision.id)

    assert result.decision_id == decision.id


# --- gate-color regression (DEEPEN task-2866) --------------------------------
#
# task-2725 DEPTH 감사가 지적한 "게이트 적색 회귀 테스트 없음"의 fake-repo로
# 가능한 절반: contracts/v1.py의 `_OUTCOME_TO_VERDICT` 표 자체는
# test_contracts_v1.py가 이미 고정하지만, explain()이 그 표를 실제로
# 그대로 경유하는지(자체적으로 outcome을 재해석하지 않는지)는 이 leaf의
# 몫이다. PAUSE_REQUIRED는 §3 "정상 주문 흐름을 DENY와 동일하게 막는다"는
# fail-closed 불변식의 핵심이라 DENY(적색)로 고정, REQUIRE_APPROVAL/
# REQUIRE_REASSESSMENT는 WARN(황색)으로 고정한다.


async def test_explain_pause_required_outcome_replays_as_deny_gate_red() -> None:
    revision = _revision()
    bundle = _bundle(revision)
    decision = _decision(
        bundle, outcome=PolicyOutcome.PAUSE_REQUIRED, reason_codes=("POLICY_DAILY_LOSS",)
    )
    repo = _repo_with(revision, bundle, decision)

    result = await explain(repo, decision.id)

    assert result.verdict == ComplianceVerdict.DENY


async def test_explain_require_approval_outcome_replays_as_warn() -> None:
    revision = _revision()
    bundle = _bundle(revision)
    decision = _decision(
        bundle, outcome=PolicyOutcome.REQUIRE_APPROVAL, reason_codes=("POLICY_NEEDS_APPROVAL",)
    )
    repo = _repo_with(revision, bundle, decision)

    result = await explain(repo, decision.id)

    assert result.verdict == ComplianceVerdict.WARN


# --- D3 adversarial (DEEPEN task-2866) ----------------------------------------


async def test_explain_result_rejects_post_construction_tampering() -> None:
    """D3 적대적 -- explain()이 반환한 `ComplianceDecision`은 `frozen=True`라
    호출부가 판정을 받은 뒤 `.verdict`를 DENY에서 ALLOW로 몰래 바꿔치기할
    수 없다(I-09)."""

    revision = _revision()
    bundle = _bundle(revision)
    decision = _decision(bundle, outcome=PolicyOutcome.DENY)
    repo = _repo_with(revision, bundle, decision)

    result = await explain(repo, decision.id)

    with pytest.raises(ValidationError):
        result.verdict = ComplianceVerdict.ALLOW  # type: ignore[misc]
