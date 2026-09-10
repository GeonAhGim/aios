"""CM-2 단위테스트 — 7종 제약(자산군·국가·통화·유동성·집중도·레버리지·ESG)이
전부 domain/exclusion.py로 표현 가능함을 증명한다. DB/HTTP 없이 순수 함수만
검증한다(I-01~I-11).

DEEPEN 2036 (task-2855, docs/audit/DEPTH_CM.md): the original CM-2 leaf graded
D1 for negative<3(2건뿐), no failure injection, no numeric performance
assertion, no gate/CI red-regression test, and no D3 adversarial/multi-instance
proof. This module is pure (no I/O), so those are added as:
  - 3 additional standalone negative (DENY) tests beyond the existing
    ESG/concentration pair (country/currency/leverage), pushing explicit
    negative assertions from 2 to 5+.
  - failure injection: monkeypatch the imported `evaluate_policy` to raise,
    and assert `evaluate_mandate_constraints` propagates instead of
    swallowing it into a silent ALLOW (fail-closed, CM-A2).
  - numeric performance: a wall-clock ceiling on 10,000 evaluations of the
    default revision/subject (pure-function regression guard).
  - gate/CI red regression: `exclusion.ALL_EXCLUSION_REASON_CODES` must equal
    the union of reason codes actually producible by triggering every branch
    — a future edit that renames/drops/adds a branch without updating the
    constant fails this test red immediately.
  - D3 adversarial + multi-instance/replay: frozen-dataclass tamper
    rejection, and byte-identical results from independent OS processes
    given the same input.
"""

import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timezone
from typing import TypedDict
from uuid import UUID, uuid4

import pytest
from typing_extensions import Unpack

import src.foundation.mandates.domain.exclusion as exclusion_module
from src.foundation.mandates.domain.exclusion import (
    ALL_EXCLUSION_REASON_CODES,
    evaluate_exclusion_lists,
    evaluate_leverage,
    evaluate_mandate_constraints,
)
from src.foundation.mandates.domain.models import (
    Autonomy,
    MandateRevision,
    MandateRevisionState,
    PolicyEvaluationSubject,
    PolicyOutcome,
)

NOW = datetime(2026, 9, 7, tzinfo=timezone.utc)

_DEFAULT_REVISION = MandateRevision(
    id=uuid4(),
    mandate_id=uuid4(),
    revision_no=1,
    state=MandateRevisionState.ACTIVE,
    max_total_exposure_pct=80.0,
    max_single_instrument_pct=20.0,
    min_cash_buffer_pct=5.0,
    max_daily_loss_pct=3.0,
    allowed_autonomy=Autonomy.PAPER,
    forbidden_assets=(),
    excluded_asset_classes=("DERIVATIVES",),
    excluded_countries=("KP",),
    excluded_currencies=("RUB",),
    min_liquidity_score=10.0,
    esg_excluded_symbols=("COAL_CO",),
    max_leverage_ratio=2.0,
)

_DEFAULT_SUBJECT = PolicyEvaluationSubject(command_type="paper_deployment")


class _RevisionOverrides(TypedDict, total=False):
    id: UUID
    mandate_id: UUID
    revision_no: int
    state: MandateRevisionState
    max_total_exposure_pct: float
    max_single_instrument_pct: float
    min_cash_buffer_pct: float
    max_daily_loss_pct: float
    allowed_autonomy: Autonomy
    forbidden_assets: tuple[str, ...]
    excluded_asset_classes: tuple[str, ...]
    excluded_countries: tuple[str, ...]
    excluded_currencies: tuple[str, ...]
    min_liquidity_score: float | None
    esg_excluded_symbols: tuple[str, ...]
    max_leverage_ratio: float | None


class _SubjectOverrides(TypedDict, total=False):
    command_type: str
    instrument_exposure_pct: float | None
    total_exposure_pct: float | None
    cash_buffer_pct: float | None
    projected_daily_loss_pct: float | None
    requested_autonomy: Autonomy | None
    asset: str | None
    asset_class: str | None
    country: str | None
    currency: str | None
    liquidity_score: float | None
    leverage_ratio: float | None


def _revision(**overrides: Unpack[_RevisionOverrides]) -> MandateRevision:
    return replace(_DEFAULT_REVISION, **overrides)


def _subject(**overrides: Unpack[_SubjectOverrides]) -> PolicyEvaluationSubject:
    return replace(_DEFAULT_SUBJECT, **overrides)


# --- ESG negative test (DoD 1) --------------------------------------------


def test_esg_excluded_symbol_is_denied():
    revision = _revision()
    subject = _subject(asset="COAL_CO")
    outcome, reasons, _ = evaluate_mandate_constraints(revision, subject)
    assert outcome == PolicyOutcome.DENY
    assert "POLICY_ESG_EXCLUDED" in reasons


def test_non_excluded_symbol_is_allowed():
    revision = _revision()
    subject = _subject(asset="CLEAN_CO")
    outcome, reasons, _ = evaluate_mandate_constraints(revision, subject)
    assert outcome == PolicyOutcome.ALLOW
    assert reasons == []


# --- Concentration boundary test (DoD 2) -----------------------------------


def test_concentration_exactly_at_limit_is_allowed():
    revision = _revision(max_single_instrument_pct=20.0)
    subject = _subject(instrument_exposure_pct=20.00)
    outcome, reasons, _ = evaluate_mandate_constraints(revision, subject)
    assert outcome == PolicyOutcome.ALLOW
    assert reasons == []


def test_concentration_one_cent_over_limit_is_denied():
    revision = _revision(max_single_instrument_pct=20.0)
    subject = _subject(instrument_exposure_pct=20.01)
    outcome, reasons, _ = evaluate_mandate_constraints(revision, subject)
    assert outcome == PolicyOutcome.DENY
    assert "POLICY_MAX_SINGLE_INSTRUMENT" in reasons


def test_liquidity_exactly_at_minimum_is_allowed():
    """min_liquidity_score와 정확히 같은 값은 '미만'이 아니므로 위반이 아니다 —
    concentration/leverage와 동일한 경계 원칙."""
    revision = _revision(min_liquidity_score=10.0)
    subject = _subject(liquidity_score=10.0)
    outcome, reasons, _ = evaluate_mandate_constraints(revision, subject)
    assert outcome == PolicyOutcome.ALLOW
    assert reasons == []


def test_asset_class_present_but_not_excluded_is_allowed():
    """subject 필드가 None이 아니라 실제 값이 있어도, 배제 목록에 없으면
    위반이 아니다 (None-처리 테스트와 별개로 멤버십 자체를 검증)."""
    revision = _revision()
    subject = _subject(asset_class="EQUITY")
    outcome, reasons, _ = evaluate_mandate_constraints(revision, subject)
    assert outcome == PolicyOutcome.ALLOW
    assert reasons == []


# --- 7 constraints, parametrized (DoD 3) -----------------------------------


CONSTRAINT_CASES = [
    pytest.param(
        {},
        {"asset_class": "DERIVATIVES"},
        "POLICY_ASSET_CLASS_EXCLUDED",
        id="asset_class",
    ),
    pytest.param({}, {"country": "KP"}, "POLICY_COUNTRY_EXCLUDED", id="country"),
    pytest.param({}, {"currency": "RUB"}, "POLICY_CURRENCY_EXCLUDED", id="currency"),
    pytest.param(
        {},
        {"liquidity_score": 5.0},
        "POLICY_LIQUIDITY_BELOW_MINIMUM",
        id="liquidity",
    ),
    pytest.param(
        {"max_single_instrument_pct": 20.0},
        {"instrument_exposure_pct": 25.0},
        "POLICY_MAX_SINGLE_INSTRUMENT",
        id="concentration",
    ),
    pytest.param(
        {"max_leverage_ratio": 2.0},
        {"leverage_ratio": 3.0},
        "POLICY_MAX_LEVERAGE",
        id="leverage",
    ),
    pytest.param({}, {"asset": "COAL_CO"}, "POLICY_ESG_EXCLUDED", id="esg"),
]


@pytest.mark.parametrize("revision_overrides,subject_overrides,expected_reason", CONSTRAINT_CASES)
def test_each_of_the_seven_constraints_is_expressible(
    revision_overrides: _RevisionOverrides,
    subject_overrides: _SubjectOverrides,
    expected_reason: str,
) -> None:
    revision = _revision(**revision_overrides)
    subject = _subject(**subject_overrides)
    outcome, reasons, _ = evaluate_mandate_constraints(revision, subject)
    assert outcome == PolicyOutcome.DENY
    assert expected_reason in reasons


# --- Sub-function unit coverage ---------------------------------------------


def test_evaluate_exclusion_lists_collects_every_violation_at_once():
    revision = _revision()
    subject = _subject(
        asset_class="DERIVATIVES",
        country="KP",
        currency="RUB",
        liquidity_score=1.0,
        asset="COAL_CO",
    )
    reasons = evaluate_exclusion_lists(revision, subject)
    assert reasons == [
        "POLICY_ASSET_CLASS_EXCLUDED",
        "POLICY_COUNTRY_EXCLUDED",
        "POLICY_CURRENCY_EXCLUDED",
        "POLICY_LIQUIDITY_BELOW_MINIMUM",
        "POLICY_ESG_EXCLUDED",
    ]


def test_evaluate_exclusion_lists_ignores_unset_subject_fields():
    """subject 필드가 None이면(아직 모르는 값) 위반으로 오판하지 않는다 —
    test_rules.py의 동일 원칙(모름 != 위반 아님)을 exclusion 쪽에도 적용."""
    revision = _revision()
    subject = _subject()
    assert evaluate_exclusion_lists(revision, subject) == []


def test_evaluate_leverage_within_limit_is_not_flagged():
    revision = _revision(max_leverage_ratio=2.0)
    subject = _subject(leverage_ratio=2.0)
    assert evaluate_leverage(revision, subject) == []


def test_pause_required_from_base_policy_still_wins_over_new_exclusion_hits():
    """일일 손실 한도 초과(PAUSE_REQUIRED)는 동시에 발생한 ESG 위반보다 여전히
    강한 조치로 채택된다(75번 §3 강도 순서, rules.py와 동일 계약)."""
    revision = _revision(max_daily_loss_pct=3.0)
    subject = _subject(projected_daily_loss_pct=5.0, asset="COAL_CO")
    outcome, reasons, _ = evaluate_mandate_constraints(revision, subject)
    assert outcome == PolicyOutcome.PAUSE_REQUIRED
    assert "POLICY_MAX_DAILY_LOSS" in reasons
    assert "POLICY_ESG_EXCLUDED" in reasons


# --- Additional standalone negative tests (DEEPEN negative<3 -> 5+) ---------


def test_country_excluded_is_denied():
    revision = _revision()
    subject = _subject(country="KP")
    outcome, reasons, _ = evaluate_mandate_constraints(revision, subject)
    assert outcome == PolicyOutcome.DENY
    assert "POLICY_COUNTRY_EXCLUDED" in reasons


def test_currency_excluded_is_denied():
    revision = _revision()
    subject = _subject(currency="RUB")
    outcome, reasons, _ = evaluate_mandate_constraints(revision, subject)
    assert outcome == PolicyOutcome.DENY
    assert "POLICY_CURRENCY_EXCLUDED" in reasons


def test_leverage_exceeded_is_denied():
    revision = _revision(max_leverage_ratio=2.0)
    subject = _subject(leverage_ratio=2.01)
    outcome, reasons, _ = evaluate_mandate_constraints(revision, subject)
    assert outcome == PolicyOutcome.DENY
    assert "POLICY_MAX_LEVERAGE" in reasons


# --- Failure injection (DEEPEN) ----------------------------------------------


def test_mandate_constraints_propagates_base_policy_failure_instead_of_silently_allowing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """실패 주입: `rules.evaluate_policy`가 내부적으로 실패하면(예: 향후
    회귀) `evaluate_mandate_constraints`는 그 예외를 삼켜 ALLOW를 조용히
    반환해서는 안 된다 — CM-A2 fail-closed는 이 조합 함수에도 적용된다."""

    def _boom(*_args: object, **_kwargs: object) -> tuple[PolicyOutcome, list[str], list[str]]:
        raise RuntimeError("simulated evaluate_policy failure")

    monkeypatch.setattr(exclusion_module, "evaluate_policy", _boom)

    revision = _revision()
    subject = _subject(asset="COAL_CO")
    with pytest.raises(RuntimeError, match="simulated evaluate_policy failure"):
        exclusion_module.evaluate_mandate_constraints(revision, subject)


# --- Numeric performance (DEEPEN) -------------------------------------------


def test_evaluate_mandate_constraints_meets_latency_budget_over_many_calls() -> None:
    """수치 성능 단언: 75번 §7 SLO는 사전 판정 경로에 p99 30ms를 배정한다.
    이 조합 함수는 그 경로 하류에서 실행되므로 그 자체가 병목이 되어서는
    안 된다. 10,000회 반복 호출의 총 지연이 넉넉한 상한(1.0s, 호출당 평균
    100us) 안에 들어야 한다 — 이후 회귀로 순수 함수가 눈에 띄게 느려지면
    이 테스트가 잡는다."""
    revision = _revision()
    subject = _subject(asset="COAL_CO", country="KP")

    started = time.perf_counter()
    for _ in range(10_000):
        evaluate_mandate_constraints(revision, subject)
    elapsed_s = time.perf_counter() - started

    assert elapsed_s < 1.0, f"10,000 evaluations took {elapsed_s:.3f}s (budget 1.0s)"


# --- Gate/CI red regression guard (DEEPEN) -----------------------------------


def test_all_exclusion_reason_codes_constant_matches_every_triggerable_branch() -> None:
    """게이트/CI 적색 회귀 가드: `exclusion.ALL_EXCLUSION_REASON_CODES`는
    `evaluate_exclusion_lists`/`evaluate_leverage`가 실제로 낼 수 있는 사유
    코드 전체와 정확히 일치해야 한다. 향후 리프가 이 상수 갱신 없이 분기를
    추가/삭제/개명하면 이 테스트가 즉시 적색이 된다(CM-1의
    `_OUTCOME_TO_VERDICT` 총량 가드와 동일 패턴)."""
    revision = _revision(max_leverage_ratio=2.0)
    subject = _subject(
        asset_class="DERIVATIVES",
        country="KP",
        currency="RUB",
        liquidity_score=1.0,
        asset="COAL_CO",
        leverage_ratio=3.0,
    )
    triggered = set(evaluate_exclusion_lists(revision, subject)) | set(
        evaluate_leverage(revision, subject)
    )
    assert triggered == ALL_EXCLUSION_REASON_CODES


# --- D3 adversarial + multi-instance/replay proof (DEEPEN) -------------------


def test_frozen_revision_and_subject_reject_post_construction_tampering() -> None:
    """D3 적대적: 컴플라이언스 판정을 좌우하는 입력을 만든 뒤 메모리에서
    필드를 바꿔치기하는 시도(다운스트림 코드의 버그 또는 공격)는 예외 없이
    조용히 성공해서는 안 된다. `MandateRevision`/`PolicyEvaluationSubject`는
    이미 `frozen=True` dataclass이므로(I-09), 그 방어선이 실제로 걸려
    있음을 실증한다."""
    revision = _revision()
    with pytest.raises(FrozenInstanceError):
        revision.excluded_countries = ()  # type: ignore[misc]

    subject = _subject(asset="COAL_CO")
    with pytest.raises(FrozenInstanceError):
        subject.asset = "CLEAN_CO"  # type: ignore[misc]


def _replay_in_subprocess(
    revision: MandateRevision, subject: PolicyEvaluationSubject
) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    """Module-level so it is picklable for `ProcessPoolExecutor` on Windows
    (spawn start method)."""
    outcome, reasons, obligations = evaluate_mandate_constraints(revision, subject)
    return outcome.value, tuple(reasons), tuple(obligations)


def test_replay_across_independent_processes_is_byte_identical() -> None:
    """D3 다중 인스턴스/리플레이 증명: 전역 상태가 완전히 분리된 별도 OS
    프로세스 3개가 동일한 revision/subject를 각자 평가해도 완전히 동일한
    (outcome, reasons, obligations)를 내야 한다 — 프로세스 지역 상태나
    임포트 순서에 우연히 기대는 비결정성이 없음을 실증한다."""
    revision = _revision(max_leverage_ratio=2.0)
    subject = _subject(asset="COAL_CO", country="KP", leverage_ratio=3.0)

    with ProcessPoolExecutor(max_workers=3) as pool:
        results = list(
            pool.map(_replay_in_subprocess, [revision, revision, revision], [subject] * 3)
        )

    assert len(results) == 3
    assert len(set(results)) == 1
