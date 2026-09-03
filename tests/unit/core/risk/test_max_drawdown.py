"""L4_risk_and_safety_v1.0.md#2.1, §8, §9 R-06 — max_drawdown 규칙 테스트."""
from decimal import Decimal

from src.core.risk.decision import RiskOutcome
from src.core.risk.inputs import EquityInputs
from src.core.risk.rules import max_drawdown
from tests.unit.core.risk._rule_test_helpers import NOW, POLICY, sample_inputs


def _inputs_with_drawdown(pct: str) -> object:
    return sample_inputs(
        equity=EquityInputs(total_equity=Decimal("10000"), drawdown_pct=Decimal(pct), as_of=NOW)
    )


def test_allow_when_no_drawdown():
    result = max_drawdown.check(_inputs_with_drawdown("0"), POLICY)
    assert result.outcome == RiskOutcome.ALLOW


def test_allow_at_warning_boundary():
    result = max_drawdown.check(_inputs_with_drawdown("10.0"), POLICY)
    assert result.outcome == RiskOutcome.ALLOW


def test_escalate_between_warning_and_hard_stop():
    result = max_drawdown.check(_inputs_with_drawdown("12.0"), POLICY)
    assert result.outcome == RiskOutcome.ESCALATE
    assert result.reason_code == "RISK_MDD_WARN"


def test_allow_at_hard_stop_boundary_since_still_within_limit():
    result = max_drawdown.check(_inputs_with_drawdown("15.0"), POLICY)
    assert result.outcome == RiskOutcome.ESCALATE


def test_deny_above_hard_stop():
    result = max_drawdown.check(_inputs_with_drawdown("16.0"), POLICY)
    assert result.outcome == RiskOutcome.DENY
    assert result.reason_code == "RISK_MDD_HARD_STOP"


def test_missing_drawdown_denies():
    inputs = sample_inputs(equity=EquityInputs(total_equity=Decimal("10000"), as_of=NOW))
    result = max_drawdown.check(inputs, POLICY)
    assert result.outcome == RiskOutcome.DENY
    assert result.missing_fields == ("equity.drawdown_pct",)
