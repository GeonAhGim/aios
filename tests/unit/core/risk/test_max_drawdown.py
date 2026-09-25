"""L4_risk_and_safety_v1.0.md#2.1, §8, §9 R-06 — max_drawdown 규칙 테스트."""
from decimal import Decimal
from typing import cast

import pytest

from src.core.loader.risk_policy_loader import RiskPolicy
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


def test_denies_at_hard_stop_boundary_plus_tiny_epsilon():
    result = max_drawdown.check(_inputs_with_drawdown("15.000001"), POLICY)
    assert result.outcome == RiskOutcome.DENY
    assert result.reason_code == "RISK_MDD_HARD_STOP"


def test_escalates_at_hard_stop_boundary_minus_tiny_epsilon():
    """A hair below hard_stop_pct=15.0 must still ESCALATE (warn), proving
    the `pct()` quantization to 6 decimal places does not round this value
    up into the hard-stop bucket."""
    result = max_drawdown.check(_inputs_with_drawdown("14.999999"), POLICY)
    assert result.outcome == RiskOutcome.ESCALATE
    assert result.reason_code == "RISK_MDD_WARN"


class _BrokenMaxDrawdownAttr:
    @property
    def hard_stop_pct(self) -> float:
        raise RuntimeError("policy backend unavailable")


class _BrokenMaxDrawdownPolicy:
    max_drawdown = _BrokenMaxDrawdownAttr()


def test_policy_max_drawdown_lookup_failure_propagates(monkeypatch: pytest.MonkeyPatch):
    broken_policy = cast(RiskPolicy, _BrokenMaxDrawdownPolicy())
    with pytest.raises(RuntimeError, match="policy backend unavailable"):
        max_drawdown.check(_inputs_with_drawdown("1.0"), broken_policy)
