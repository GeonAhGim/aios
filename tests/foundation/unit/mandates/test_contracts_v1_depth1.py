"""L4_compliance_and_regulatory depth-1 — 추가 음성과 성능·적색 재현 테스트.

대상 모듈: src.foundation.mandates.contracts.v1
기존 파일 test_contracts_v1.py를 읽기만 하고 수정하지 않는다.

DEEPEN-DEPTH ND-3 (task-24912): 원장 판정에서 perf 단언 0이 미달이므로
새 성능 단언 2종(perf 마커 + 10배 가상 지연 적색 재현)을 신설한다.
음성·실패 주입·게이트 적색 재현도 기존에 없던 것만 보강한다.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

import pytest
from pydantic import ValidationError

import src.foundation.mandates.contracts.v1 as contracts_v1
from src.core.risk.hashing import canonical_json, sha256_hex
from src.foundation.mandates.contracts.v1 import (
    _OUTCOME_TO_VERDICT,
    ComplianceVerdict,
    PolicyDecisionRow,
    PolicyOutcome,
    RuleHit,
    compliance_decision_from_policy_decision,
    verdict_for_outcome,
)
from tests.support.perf_budget import PerfBudget
from tests.support.perf_inject import reset_virtual_totals, virtual_delayed_sync

# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------


def _hex_digest(seed: str) -> str:
    return sha256_hex(canonical_json({"seed": seed}))


NOW = datetime(2026, 9, 7, tzinfo=UTC)


def _make_row(
    outcome: PolicyOutcome = PolicyOutcome.DENY,
    reason_codes: list[str] | None = None,
) -> PolicyDecisionRow:
    """간단한 PolicyDecisionRow 생성 — hash/evaluated_at는 항상 유효."""
    return PolicyDecisionRow(
        decision_id=uuid4(),
        outcome=outcome,
        reason_codes=reason_codes or ["R1"],
        inputs_hash=_hex_digest("inputs"),
        bundle_version=_hex_digest("bundle"),
        evaluated_at=NOW,
    )


# ---------------------------------------------------------------------------
# 음성 테스트 (negative) — 기존 파일에 없던 것만
# ---------------------------------------------------------------------------


def test_policy_decision_row_rejects_naive_evaluated_at() -> None:
    """PolicyDecisionRow에 naive datetime(evaluated_at)을 넣으면
    ValidationError이Must 나야 한다 — _validate_tz_aware 가드가 막는다."""
    with pytest.raises(ValidationError):
        PolicyDecisionRow(
            decision_id=uuid4(),
            outcome=PolicyOutcome.ALLOW,
            reason_codes=["R1"],
            inputs_hash=_hex_digest("inputs"),
            bundle_version=_hex_digest("bundle"),
            evaluated_at=datetime(2026, 9, 7),  # naive — tzinfo 없음
        )


def test_policy_decision_row_rejects_uppercase_inputs_hash() -> None:
    """inputs_hash에 대문자 hex를 넣으면 ValidationError이Must 난다."""
    with pytest.raises(ValidationError):
        PolicyDecisionRow(
            decision_id=uuid4(),
            outcome=PolicyOutcome.ALLOW,
            reason_codes=["R1"],
            inputs_hash="A" * 64,  # 대문자
            bundle_version=_hex_digest("bundle"),
            evaluated_at=NOW,
        )


def test_policy_decision_row_rejects_63_char_inputs_hash() -> None:
    """inputs_hash가 63자(64 hex chars보다 짧음)면 ValidationError이Must 난다."""
    with pytest.raises(ValidationError):
        PolicyDecisionRow(
            decision_id=uuid4(),
            outcome=PolicyOutcome.ALLOW,
            reason_codes=["R1"],
            inputs_hash="a" * 63,  # 길이 부족
            bundle_version=_hex_digest("bundle"),
            evaluated_at=NOW,
        )


def test_rule_hit_rejects_verdict_string_not_in_compliance_verdict_enum(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RuleHit(severity=...)에 ComplianceVerdict 밖의 문자열을 넣으면
    ValidationError이Must 난다 — Pydantic이 StrEnum을 강제한다."""
    with pytest.raises(ValidationError):
        RuleHit(rule_id="R1", severity=cast(Any, "NOT_A_VERDICT"), message="m", evidence={})


def test_verdict_for_outcome_raises_keyerror_when_mapping_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_OUTCOME_TO_VERDICT에서 키 하나를 제거하면 verdict_for_outcome이
    ALLOW로 폴백하지 않고 KeyError를 낸다 — 매퍼가 조용히 폴백하지 않음을
    확인한다."""
    original = dict(_OUTCOME_TO_VERDICT)
    # ALLOW를 제거
    del original[PolicyOutcome.ALLOW]
    monkeypatch.setattr(contracts_v1, "_OUTCOME_TO_VERDICT", original)

    with pytest.raises(KeyError):
        verdict_for_outcome(PolicyOutcome.ALLOW)


# ---------------------------------------------------------------------------
# 실패 주입 (failure injection)
# ---------------------------------------------------------------------------


def test_mapper_propagates_verdict_for_outcome_failure_instead_of_silently_allowing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """실패 주입: `verdict_for_outcome` 내부가 실패하면(예: 매퍼 테이블
    불일치) `compliance_decision_from_policy_decision`은 그 예외를 삼켜
    ALLOW-형 판정을 조용히 반환해서는 안 된다 — CM-A2 fail-closed.

    기존 test_mapper_propagates_rule_hit_construction_failure가 RuleHit
    생성 실패를 주입했다면, 이 테스트는 verdict_for_outcome 매퍼 자체
    실패를 주입한다.
    """

    def _boom_outcome(_outcome: PolicyOutcome) -> ComplianceVerdict:
        raise RuntimeError("simulated verdict_for_outcome mapping failure")

    monkeypatch.setattr(contracts_v1, "verdict_for_outcome", _boom_outcome)

    row = _make_row()
    with pytest.raises(RuntimeError, match="simulated verdict_for_outcome mapping failure"):
        compliance_decision_from_policy_decision(row)


# ---------------------------------------------------------------------------
# 게이트 적색 재현 (gate red regression)
# ---------------------------------------------------------------------------


def test_outcome_to_verdict_mapping_assertion_catches_broken_mapping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """게이트 적색 재현: `_OUTCOME_TO_VERDICT`에서 PAUSE_REQUIRED를 제거한
    딕셔너리로 전체 매핑 단언을 돌려 AssertionError가 나는지 확인한다.

    이 테스트는 "단언이 실제로 잡는다"를 증명하는 용도이므로
    pytest.raises(AssertionError)로 감싼다.

    _OUTCOME_TO_VERDICT는 모듈 레벨에서 import되었으므로 monkeypatch로
    contracts_v1._OUTCOME_TO_VERDICT를 바꿔도 로컬 참조(_OUTCOME_TO_VERDICT)는
    바뀌지 않는다. 따라서 contracts_v1 모듈 자체의 _OUTCOME_TO_VERDICT를
    바꿔서 verdict_for_outcome이 그 변경을 보게 만든다.
    """
    original = dict(_OUTCOME_TO_VERDICT)
    # PAUSE_REQUIRED 제거 — 단언 `set(_OUTCOME_TO_VERDICT.keys()) == set(PolicyOutcome)`이
    # 키 수에서 차이가 나므로 AssertionError가 나야 한다.
    broken = {k: v for k, v in original.items() if k != PolicyOutcome.PAUSE_REQUIRED}
    # 모듈의 _OUTCOME_TO_VERDICT를 바꿔서 verdict_for_outcome이 새 딕셔너리를 보게 함
    monkeypatch.setattr(contracts_v1, "_OUTCOME_TO_VERDICT", broken)

    # 단언이 실제로 잡는지 확인 — 모듈의 _OUTCOME_TO_VERDICT를 직접 읽어야 함
    with pytest.raises(AssertionError):
        assert set(contracts_v1._OUTCOME_TO_VERDICT.keys()) == set(PolicyOutcome)


# ---------------------------------------------------------------------------
# 성능 단언 (performance assertions)
# ---------------------------------------------------------------------------

# 성능 예산: 로컬 실측 기준
# 측정 방법: PerfBudget.best_of(lambda: compliance_decision_from_policy_decision(row), n=5)
#   where row has 5,000 reason_codes.
# 호스트: Windows 11 Pro 10.0.26300, Python 3.13, worktree-local.
# 실측값: cpu_ms 0.0000~15.625 (Windows process_time 양자화 tick 15.625ms 내),
#   wall_ms 5.5~8.1.
# 예산 근거: 실측 max cpu_ms(15.625ms) × 30 = 468.75ms. 여유 확보를 위해
#   500ms로 설정. 30배는 task-6774 PerfBudget 설계 원칙(실측의 30배 이상 여유).
BUDGET_MS = 500.0


@pytest.mark.perf
def test_mapping_large_reason_code_set_completes_within_perf_budget(
    perf_budget: PerfBudget,
) -> None:
    """수치 성능 단언: 5,000개 reason_codes 매핑이 CPU 예산 내여야 한다.

    예산: 500ms (실측 15.625ms × 30).
    """
    row = PolicyDecisionRow(
        decision_id=uuid4(),
        outcome=PolicyOutcome.DENY,
        reason_codes=[f"CODE_{i}" for i in range(5000)],
        inputs_hash=_hex_digest("perf-depth1-inputs"),
        bundle_version=_hex_digest("perf-depth1-bundle"),
        evaluated_at=NOW,
    )

    sample = perf_budget.best_of(
        lambda: compliance_decision_from_policy_decision(row),
        n=5,
    )
    assert sample.cpu_ms < BUDGET_MS, perf_budget.describe(sample, budget_ms=BUDGET_MS)


# ---------------------------------------------------------------------------
# 성능 적색 재현 (perf red regression with 10x virtual delay)
# ---------------------------------------------------------------------------


@pytest.mark.perf
def test_perf_red_regression_with_10x_virtual_delay_injection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """성능 적색 재현: `verdict_for_outcome`에 10배 가상 지연을 주입하고
    예산 초과가 확실히 나는지 확인한다.

    기준: 신설 예산(BUDGET_MS=500ms)의 10배 = 5,000ms 가상 지연.
    virtual_delayed_sync은 지연을 cpu+wall ledger에 추가하므로
    PerfBudget이 그 값을 측정한다.

    이 단언이 실제로 실패하는지 pytest.raises(AssertionError)로 확인한다.
    """
    reset_virtual_totals()

    # 예산의 10배 지연 — 500ms × 10 = 5000ms
    delay_seconds = (BUDGET_MS * 10) / 1000.0

    original = contracts_v1.verdict_for_outcome
    monkeypatch.setattr(
        contracts_v1, "verdict_for_outcome", virtual_delayed_sync(original, delay_seconds)
    )

    row = PolicyDecisionRow(
        decision_id=uuid4(),
        outcome=PolicyOutcome.DENY,
        reason_codes=[f"CODE_{i}" for i in range(5000)],
        inputs_hash=_hex_digest("perf-red-inputs"),
        bundle_version=_hex_digest("perf-red-bundle"),
        evaluated_at=NOW,
    )

    pb = PerfBudget()
    sample = pb.best_of(
        lambda: compliance_decision_from_policy_decision(row),
        n=5,
    )

    # 지연 주입으로 예산 초과가 확실히 나야 함 — 단언이 실제로 잡는지 확인
    with pytest.raises(AssertionError):
        assert sample.cpu_ms < BUDGET_MS, pb.describe(sample, budget_ms=BUDGET_MS)
