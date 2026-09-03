"""L4_risk_and_safety_v1.0.md#2.1, §8, §9 R-08 — concentration 규칙 테스트.

DoD: "체결 후 예측 비중" — 기존 30% + 신규 5% → 35% 거부, 감소 주문은 통과.
"""
from decimal import Decimal

from src.core.risk.decision import RiskOutcome
from src.core.risk.inputs import EquityInputs, ExposureSnapshot
from src.core.risk.rules import concentration
from tests.unit.core.risk._rule_test_helpers import NOW, POLICY, sample_inputs


def _inputs(
    *, symbol_market_value: str | None, notional: str, total_equity: str | None, reduce_only: bool
) -> object:
    return sample_inputs(
        intent=sample_inputs().intent.model_copy(
            update={"notional": Decimal(notional), "reduce_only": reduce_only}
        ),
        equity=EquityInputs(
            total_equity=Decimal(total_equity) if total_equity is not None else None, as_of=NOW
        ),
        exposure=ExposureSnapshot(
            position_quantity=Decimal("0"),
            symbol_market_value=(
                Decimal(symbol_market_value) if symbol_market_value is not None else None
            ),
            as_of=NOW,
        ),
    )


def test_existing_30pct_plus_new_5pct_denies():
    inputs = _inputs(
        symbol_market_value="3000", notional="500", total_equity="10000", reduce_only=False
    )
    result = concentration.check(inputs, POLICY)
    assert result.outcome == RiskOutcome.DENY
    assert result.reason_code == "RISK_CONCENTRATION_EXCEEDED"
    assert result.observed == Decimal("35.000000")


def test_allow_at_boundary():
    inputs = _inputs(
        symbol_market_value="1500", notional="500", total_equity="10000", reduce_only=False
    )
    result = concentration.check(inputs, POLICY)
    assert result.outcome == RiskOutcome.ALLOW


def test_reduce_only_order_always_passes():
    inputs = _inputs(
        symbol_market_value="9000", notional="9000", total_equity="10000", reduce_only=True
    )
    result = concentration.check(inputs, POLICY)
    assert result.outcome == RiskOutcome.ALLOW


def test_reduce_only_bypasses_missing_data():
    inputs = _inputs(
        symbol_market_value=None, notional="500", total_equity=None, reduce_only=True
    )
    result = concentration.check(inputs, POLICY)
    assert result.outcome == RiskOutcome.ALLOW


def test_missing_total_equity_denies():
    inputs = _inputs(
        symbol_market_value="3000", notional="500", total_equity=None, reduce_only=False
    )
    result = concentration.check(inputs, POLICY)
    assert result.outcome == RiskOutcome.DENY
    assert result.missing_fields == ("equity.total_equity",)


def test_missing_symbol_market_value_denies():
    inputs = _inputs(
        symbol_market_value=None, notional="500", total_equity="10000", reduce_only=False
    )
    result = concentration.check(inputs, POLICY)
    assert result.outcome == RiskOutcome.DENY
    assert result.missing_fields == ("exposure.symbol_market_value",)
