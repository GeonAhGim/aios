"""L4_risk_and_safety_v1.0.md#2.1, §9 R-08 — 체결 후 예측 비중 집중도.

기존 시가평가(`exposure.symbol_market_value`) + 신규 주문 notional
(`intent.notional`)의 합을 `equity.total_equity`로 나눈 "체결 후 예측
비중"이 `single_asset_max_pct`를 넘으면 DENY(`RISK_CONCENTRATION_EXCEEDED`).
감소 주문(`intent.reduce_only`)은 노출을 줄이므로 다른 입력과 무관하게
항상 통과한다(§2.1 원문 — 예: 기존 30% + 신규 5% → 35% 초과는 거부되지만
같은 주문이 reduce_only면 통과). reduce_only가 아닐 때 입력 결손은
base.missing()으로 DENY(I2).
"""
from __future__ import annotations

from decimal import Decimal

from src.core.loader.risk_policy_loader import RiskPolicy
from src.core.risk.decision import RiskOutcome, RuleResult
from src.core.risk.inputs import RiskInputs
from src.core.risk.rules.base import missing, pct

RULE_ID = "concentration"
_ZERO = Decimal("0")


def check(inputs: RiskInputs, policy: RiskPolicy) -> RuleResult:
    if inputs.intent.reduce_only:
        return RuleResult(rule_id=RULE_ID, outcome=RiskOutcome.ALLOW, unit="pct")

    total_equity = inputs.equity.total_equity
    if total_equity is None or total_equity <= _ZERO:
        return missing(RULE_ID, "equity.total_equity")

    symbol_market_value = inputs.exposure.symbol_market_value
    if symbol_market_value is None:
        return missing(RULE_ID, "exposure.symbol_market_value")

    projected_notional = abs(symbol_market_value) + inputs.intent.notional
    projected_pct = pct((projected_notional / total_equity) * Decimal("100"))
    limit = pct(Decimal(str(policy.position_concentration.single_asset_max_pct)))

    if projected_pct > limit:
        return RuleResult(
            rule_id=RULE_ID,
            outcome=RiskOutcome.DENY,
            reason_code="RISK_CONCENTRATION_EXCEEDED",
            observed=projected_pct,
            limit=limit,
            unit="pct",
        )
    return RuleResult(
        rule_id=RULE_ID,
        outcome=RiskOutcome.ALLOW,
        observed=projected_pct,
        limit=limit,
        unit="pct",
    )
