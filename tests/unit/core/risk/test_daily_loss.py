"""L4_risk_and_safety_v1.0.md#2.1, §8, §9 R-05 — daily_loss 규칙 테스트."""
from decimal import Decimal

from src.core.risk.decision import RiskOutcome
from src.core.risk.inputs import EquityInputs
from src.core.risk.rules import daily_loss
from tests.unit.core.risk._rule_test_helpers import NOW, POLICY, sample_inputs


def _inputs_with_daily_pnl(pct: str) -> object:
    return sample_inputs(
        equity=EquityInputs(total_equity=Decimal("10000"), daily_pnl_pct=Decimal(pct), as_of=NOW)
    )


def test_allow_when_profit():
    result = daily_loss.check(_inputs_with_daily_pnl("2.0"), POLICY)
    assert result.outcome == RiskOutcome.ALLOW


def test_allow_at_warning_boundary():
    # 경계값(=한도) 자체는 통과한다.
    result = daily_loss.check(_inputs_with_daily_pnl("-3.0"), POLICY)
    assert result.outcome == RiskOutcome.ALLOW


def test_escalate_between_warning_and_halt():
    result = daily_loss.check(_inputs_with_daily_pnl("-4.0"), POLICY)
    assert result.outcome == RiskOutcome.ESCALATE
    assert result.reason_code == "RISK_DAILY_LOSS_WARN"


def test_allow_at_halt_boundary_since_still_within_limit():
    # halt_pct(5.0) 경계는 초과가 아니므로 warning 구간의 ESCALATE에 머문다.
    result = daily_loss.check(_inputs_with_daily_pnl("-5.0"), POLICY)
    assert result.outcome == RiskOutcome.ESCALATE


def test_deny_above_halt():
    result = daily_loss.check(_inputs_with_daily_pnl("-6.0"), POLICY)
    assert result.outcome == RiskOutcome.DENY
    assert result.reason_code == "RISK_DAILY_LOSS_HALT"


def test_missing_daily_pnl_denies():
    inputs = sample_inputs(equity=EquityInputs(total_equity=Decimal("10000"), as_of=NOW))
    result = daily_loss.check(inputs, POLICY)
    assert result.outcome == RiskOutcome.DENY
    assert result.missing_fields == ("equity.daily_pnl_pct",)
