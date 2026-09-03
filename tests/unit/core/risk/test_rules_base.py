"""L4_risk_and_safety_v1.0.md#2.1, §9 R-04 — rules/base.py 계약 테스트."""
from decimal import Decimal

import pytest
from pydantic import ValidationError

from src.core.risk.decision import RiskOutcome, RuleResult
from src.core.risk.rules.base import Rule, missing, pct, rule_error


def test_missing_denies_and_fills_missing_fields():
    result = missing("daily_loss", "equity.daily_pnl_pct")

    assert result.outcome == RiskOutcome.DENY
    assert result.missing_fields == ("equity.daily_pnl_pct",)
    assert result.reason_code == "RISK_INPUT_MISSING:equity.daily_pnl_pct"
    assert result.unit == "pct"


def test_missing_accepts_explicit_unit():
    result = missing("leverage", "exposure.gross_leverage", unit="x")
    assert result.unit == "x"


def test_ruleresult_forbids_missing_fields_without_deny():
    # I2 — 결손 필드가 있으면서 ALLOW로 새는 것은 금지된다(fail-closed).
    with pytest.raises(ValidationError):
        RuleResult(
            rule_id="daily_loss",
            outcome=RiskOutcome.ALLOW,
            unit="pct",
            missing_fields=("equity.daily_pnl_pct",),
        )


def test_rule_error_denies_with_risk_rule_error_reason_code():
    def _broken_rule() -> RuleResult:
        raise ZeroDivisionError("boom")

    try:
        _broken_rule()
        result = None
    except ZeroDivisionError:
        # evaluator(R-16)가 규칙 예외를 잡아 이 헬퍼로 DENY를 만든다(I2).
        result = rule_error("daily_loss")

    assert result is not None
    assert result.outcome == RiskOutcome.DENY
    assert result.reason_code == "RISK_RULE_ERROR:daily_loss"
    assert result.missing_fields == ()


def test_pct_quantizes_to_six_decimals():
    assert pct(Decimal("12.3")) == Decimal("12.300000")
    assert pct(Decimal("1.123456789")) == Decimal("1.123457")


def test_pct_rounds_half_to_even_at_precision_boundary():
    assert pct(Decimal("1.0000005")) == Decimal("1.000000")
    assert pct(Decimal("1.0000015")) == Decimal("1.000002")


def test_rule_protocol_matches_plain_function_signature():
    def _allow_all(inputs: object, policy: object) -> RuleResult:
        return RuleResult(rule_id="noop", outcome=RiskOutcome.ALLOW, unit="pct")

    conforming: Rule = _allow_all  # mypy가 시그니처 불일치를 잡아낸다
    result = conforming(object(), object())  # type: ignore[arg-type]
    assert result.outcome == RiskOutcome.ALLOW
