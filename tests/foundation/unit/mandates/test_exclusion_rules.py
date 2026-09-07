"""CM-2 단위테스트 — 7종 제약(자산군·국가·통화·유동성·집중도·레버리지·ESG)이
전부 domain/exclusion.py로 표현 가능함을 증명한다. DB/HTTP 없이 순수 함수만
검증한다(I-01~I-11)."""
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from src.foundation.mandates.domain.exclusion import (
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


def _revision(**overrides: object) -> MandateRevision:
    defaults: dict[str, object] = dict(
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
    defaults.update(overrides)
    return MandateRevision(**defaults)  # type: ignore[arg-type]


def _subject(**overrides: object) -> PolicyEvaluationSubject:
    defaults: dict[str, object] = dict(command_type="paper_deployment")
    defaults.update(overrides)
    return PolicyEvaluationSubject(**defaults)  # type: ignore[arg-type]


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
    revision_overrides: dict[str, object],
    subject_overrides: dict[str, object],
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
