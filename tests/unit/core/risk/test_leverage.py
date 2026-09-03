"""L4_risk_and_safety_v1.0.md#2.1, §8, §9 R-07 — leverage 규칙 테스트."""
from decimal import Decimal

from src.core.risk.decision import RiskOutcome
from src.core.risk.inputs import ExposureSnapshot
from src.core.risk.rules import leverage
from tests.unit.core.risk._rule_test_helpers import NOW, POLICY, sample_inputs


def _inputs_with_gross_leverage(value: str | None) -> object:
    gross_leverage = Decimal(value) if value is not None else None
    return sample_inputs(
        exposure=ExposureSnapshot(
            position_quantity=Decimal("0"), gross_leverage=gross_leverage, as_of=NOW
        )
    )


def test_allow_at_default_max_boundary():
    result = leverage.check(_inputs_with_gross_leverage("3.0"), POLICY)
    assert result.outcome == RiskOutcome.ALLOW


def test_allow_below_default_max():
    result = leverage.check(_inputs_with_gross_leverage("1.5"), POLICY)
    assert result.outcome == RiskOutcome.ALLOW


def test_deny_above_default_max():
    result = leverage.check(_inputs_with_gross_leverage("3.5"), POLICY)
    assert result.outcome == RiskOutcome.DENY
    assert result.reason_code == "RISK_LEVERAGE_EXCEEDED"
    assert result.unit == "x"


def test_missing_gross_leverage_denies():
    result = leverage.check(_inputs_with_gross_leverage(None), POLICY)
    assert result.outcome == RiskOutcome.DENY
    assert result.missing_fields == ("exposure.gross_leverage",)
    assert result.unit == "x"
