"""L4_compliance_and_regulatory_v1.0.md §9 CM-1 — ComplianceDecision/RuleHit
contract tests. No DB, no new table: these map onto the existing
`policy_decision`/`policy_bundle` rows only.

DEEPEN 2035 (task-2854, docs/audit/DEPTH_CM.md): the original CM-1 leaf
graded D1 for missing failure injection, numeric performance assertions, a
gate/CI red-regression test, and D3 adversarial/multi-instance/replay proof.
This module is pure (no I/O), so those four are adapted to what a pure
dataclass/mapper module can actually exercise:
  - failure injection: monkeypatch `RuleHit` to raise inside the mapper and
    assert the exception propagates instead of being swallowed into a
    silent ALLOW (`test_mapper_propagates_rule_hit_construction_failure...`).
  - numeric performance: a wall-clock ceiling on mapping a 5,000-entry
    reason_codes list (`test_mapping_large_reason_code_set...`).
  - gate/CI red regression: `_OUTCOME_TO_VERDICT` must stay total over
    `PolicyOutcome` — a future enum member added without a mapping entry
    fails this test red (`test_outcome_to_verdict_mapping_total_over...`).
  - D3 adversarial + multi-instance/replay: frozen-model tamper rejection,
    and byte-identical output from independent OS processes given the same
    input (`test_frozen_...`, `test_replay_across_independent_processes...`).
"""

import time
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

import src.foundation.mandates.contracts.v1 as contracts_v1
from src.core.risk.hashing import canonical_json, sha256_hex
from src.foundation.mandates.contracts.v1 import (
    _OUTCOME_TO_VERDICT,
    ComplianceDecision,
    ComplianceVerdict,
    PolicyDecisionRow,
    PolicyOutcome,
    RuleHit,
    compliance_decision_from_policy_decision,
    verdict_for_outcome,
)

NOW = datetime(2026, 9, 7, tzinfo=timezone.utc)


def _hex_digest(seed: str) -> str:
    # R-01 reuse (canonical_json + sha256_hex) — never hand-rolled hashlib.
    return sha256_hex(canonical_json({"seed": seed}))


def test_rejects_verdict_outside_allow_warn_deny() -> None:
    with pytest.raises(ValidationError):
        ComplianceDecision(
            decision_id=uuid4(),
            verdict="ALLOWED",  # not in the ALLOW|WARN|DENY table
            rule_hits=[],
            inputs_hash=_hex_digest("inputs"),
            bundle_version=_hex_digest("bundle"),
            evaluated_at=NOW,
        )


def test_rejects_non_sha256_inputs_hash() -> None:
    with pytest.raises(ValidationError):
        ComplianceDecision(
            decision_id=uuid4(),
            verdict=ComplianceVerdict.ALLOW,
            rule_hits=[],
            inputs_hash="not-a-hash",
            bundle_version=_hex_digest("bundle"),
            evaluated_at=NOW,
        )


def test_rejects_naive_evaluated_at() -> None:
    with pytest.raises(ValidationError):
        ComplianceDecision(
            decision_id=uuid4(),
            verdict=ComplianceVerdict.ALLOW,
            rule_hits=[],
            inputs_hash=_hex_digest("inputs"),
            bundle_version=_hex_digest("bundle"),
            evaluated_at=datetime(2026, 9, 7),  # naive
        )


def test_outcome_to_verdict_mapping_is_total_and_fail_closed() -> None:
    assert verdict_for_outcome(PolicyOutcome.ALLOW) == ComplianceVerdict.ALLOW
    assert verdict_for_outcome(PolicyOutcome.DENY) == ComplianceVerdict.DENY
    assert verdict_for_outcome(PolicyOutcome.REQUIRE_APPROVAL) == ComplianceVerdict.WARN
    assert verdict_for_outcome(PolicyOutcome.REQUIRE_REASSESSMENT) == ComplianceVerdict.WARN
    # PAUSE_REQUIRED blocks the order flow just like DENY — must not degrade to WARN.
    assert verdict_for_outcome(PolicyOutcome.PAUSE_REQUIRED) == ComplianceVerdict.DENY


def test_round_trip_from_existing_policy_decision_row() -> None:
    """Simulates one existing `policy_decision` row (as read from Postgres)
    and asserts every ComplianceDecision field is equivalent to the source
    row's field — no new table, no silent data loss for the fields CM-1's
    contract carries.
    """
    row_id = uuid4()
    row_command_fingerprint = _hex_digest("tenant+subject+asset")
    row_reason_codes = ["POLICY_MAX_TOTAL_EXPOSURE", "POLICY_MAX_SINGLE_INSTRUMENT"]
    bundle_rule_hash = _hex_digest("revision-rules")

    row = PolicyDecisionRow(
        decision_id=row_id,
        outcome=PolicyOutcome.DENY,
        reason_codes=row_reason_codes,
        inputs_hash=row_command_fingerprint,
        bundle_version=bundle_rule_hash,
        evaluated_at=NOW,
    )
    decision = compliance_decision_from_policy_decision(row)

    assert decision.decision_id == row_id
    assert decision.inputs_hash == row_command_fingerprint
    assert decision.bundle_version == bundle_rule_hash
    assert decision.evaluated_at == NOW
    assert decision.verdict == ComplianceVerdict.DENY
    assert [hit.rule_id for hit in decision.rule_hits] == row_reason_codes
    assert all(hit.severity == ComplianceVerdict.DENY for hit in decision.rule_hits)
    assert all(isinstance(hit, RuleHit) for hit in decision.rule_hits)


def test_round_trip_allow_outcome_has_no_rule_hits() -> None:
    row_id = uuid4()
    row_command_fingerprint = _hex_digest("allow-case")
    bundle_rule_hash = _hex_digest("revision-rules-allow")

    row = PolicyDecisionRow(
        decision_id=row_id,
        outcome=PolicyOutcome.ALLOW,
        reason_codes=[],
        inputs_hash=row_command_fingerprint,
        bundle_version=bundle_rule_hash,
        evaluated_at=NOW,
    )
    decision = compliance_decision_from_policy_decision(row)

    assert decision.decision_id == row_id
    assert decision.verdict == ComplianceVerdict.ALLOW
    assert decision.rule_hits == []
    assert decision.inputs_hash == row_command_fingerprint
    assert decision.bundle_version == bundle_rule_hash


def test_frozen_decision_and_rule_hit_reject_post_construction_tampering() -> None:
    """D3 적대적: 감사 판정을 만든 뒤 메모리에서 `.verdict`를 DENY->ALLOW로
    바꿔치기하는 시도(다운스트림 코드의 버그 또는 공격)는 예외 없이 조용히
    성공해서는 안 된다. `core/risk/decision.RiskDecision`/`RuleResult`가
    이미 `frozen=True`인 것과 동일한 방어선(I-09 두 권위 모두 변조 불가).
    """
    decision = ComplianceDecision(
        decision_id=uuid4(),
        verdict=ComplianceVerdict.DENY,
        rule_hits=[],
        inputs_hash=_hex_digest("tamper-decision"),
        bundle_version=_hex_digest("tamper-bundle"),
        evaluated_at=NOW,
    )
    with pytest.raises(ValidationError):
        decision.verdict = ComplianceVerdict.ALLOW  # type: ignore[misc]

    hit = RuleHit(rule_id="R1", severity=ComplianceVerdict.DENY, message="m", evidence={})
    with pytest.raises(ValidationError):
        hit.severity = ComplianceVerdict.ALLOW  # type: ignore[misc]


def test_mapper_propagates_rule_hit_construction_failure_instead_of_silently_allowing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """실패 주입: `RuleHit` 생성이 내부적으로 실패하면(예: 향후 스키마
    변경으로 인한 회귀) `compliance_decision_from_policy_decision`은 그
    예외를 삼켜 ALLOW-형 판정을 조용히 반환해서는 안 된다 — CM-A2
    fail-closed는 CM-3 evaluator뿐 아니라 CM-1 매퍼 자체에도 적용된다.
    """

    def _boom(*_args: object, **_kwargs: object) -> RuleHit:
        raise RuntimeError("simulated RuleHit construction failure")

    monkeypatch.setattr(contracts_v1, "RuleHit", _boom)

    row = PolicyDecisionRow(
        decision_id=uuid4(),
        outcome=PolicyOutcome.DENY,
        reason_codes=["SOME_CODE"],
        inputs_hash=_hex_digest("failure-injection"),
        bundle_version=_hex_digest("failure-injection-bundle"),
        evaluated_at=NOW,
    )
    with pytest.raises(RuntimeError, match="simulated RuleHit construction failure"):
        contracts_v1.compliance_decision_from_policy_decision(row)


def test_mapping_large_reason_code_set_completes_within_latency_budget() -> None:
    """수치 성능 단언: §7 SLO는 사전 판정 경로에 p99 30ms를 배정한다. CM-1의
    매퍼는 그 경로 하류에서 실행되므로 그 자체가 병목이 되어서는 안 된다.
    5,000개 reason_codes는 실제 규칙 번들(`domain/rule_bundle.py`, 규칙
    수십 개 수준)의 100배 이상이라 이 임계값은 여유 있는 상한이지, SLO를
    그대로 복제한 값은 아니다.
    """
    row = PolicyDecisionRow(
        decision_id=uuid4(),
        outcome=PolicyOutcome.DENY,
        reason_codes=[f"CODE_{i}" for i in range(5000)],
        inputs_hash=_hex_digest("perf-inputs"),
        bundle_version=_hex_digest("perf-bundle"),
        evaluated_at=NOW,
    )

    started = time.perf_counter()
    decision = compliance_decision_from_policy_decision(row)
    elapsed_s = time.perf_counter() - started

    assert len(decision.rule_hits) == 5000
    assert elapsed_s < 1.0, f"mapping 5,000 reason_codes took {elapsed_s:.3f}s (budget 1.0s)"


def test_outcome_to_verdict_mapping_total_over_enum_members_ci_guard() -> None:
    """게이트/CI 적색 회귀 가드: `_OUTCOME_TO_VERDICT`는 `PolicyOutcome`의
    모든 멤버를 정확히 1개씩 커버해야 한다. 향후 리프가 매핑 갱신 없이
    `PolicyOutcome`에 새 값을 추가하면, `verdict_for_outcome`이 런타임에
    `KeyError`로 죽거나(운영 중단) `.get(..., ALLOW)` 식으로 fail-open
    쪽으로 조용히 리팩터될 위험이 있다 — 이 테스트가 그 드리프트를 CI에서
    즉시 적색으로 잡는다.
    """
    assert set(_OUTCOME_TO_VERDICT.keys()) == set(PolicyOutcome)


def _replay_in_subprocess(row: PolicyDecisionRow) -> str:
    """Module-level so it is picklable for `ProcessPoolExecutor` on
    Windows (spawn start method)."""
    decision = compliance_decision_from_policy_decision(row)
    return decision.model_dump_json()


def test_replay_across_independent_processes_is_byte_identical() -> None:
    """D3 다중 인스턴스/리플레이 증명: 전역 상태가 완전히 분리된 별도 OS
    프로세스 3개가 동일한 `PolicyDecisionRow`를 각자 매핑해도 바이트 단위로
    동일한 `ComplianceDecision`을 내야 한다 — 프로세스 지역 캐시나 임포트
    순서에 우연히 기대는 비결정성이 없음을 실증한다(CM-A4 재현성이 단일
    프로세스에 국한되지 않음).
    """
    row = PolicyDecisionRow(
        decision_id=uuid4(),
        outcome=PolicyOutcome.DENY,
        reason_codes=["A", "B", "C"],
        inputs_hash=_hex_digest("replay-inputs"),
        bundle_version=_hex_digest("replay-bundle"),
        evaluated_at=NOW,
    )

    with ProcessPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(_replay_in_subprocess, [row, row, row]))

    assert len(results) == 3
    assert len(set(results)) == 1
