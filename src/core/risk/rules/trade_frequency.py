"""L4_risk_and_safety_v1.0.md#2.1, §9 R-12 — 거래 빈도 이상 판정.

1시간 체결 건수가 `max(24h 평균 × anomaly_multiplier, max_trades_per_hour)`를
넘으면 DENY한다 — 배수만 쓰면 평소 거래가 적은 전략의 이상 탐지가 무뎌지고,
절대 상한만 쓰면 원래 활발한 전략을 오탐하므로 둘 중 큰 값을 상한으로
취한다(§9 R-12 DoD). 순수(I/O 금지), 상한 60줄.
"""
from __future__ import annotations

from decimal import Decimal

from src.core.loader.risk_policy_loader import RiskPolicy
from src.core.risk.decision import RiskOutcome, RuleResult
from src.core.risk.inputs import RiskInputs
from src.core.risk.rules.base import missing

_RULE_ID = "trade_frequency"


def trade_frequency(inputs: RiskInputs, policy: RiskPolicy) -> RuleResult:
    activity = inputs.activity

    if activity.trades_last_1h is None:
        return missing(_RULE_ID, "activity.trades_last_1h", unit="count")
    if activity.trades_avg_per_hour_24h is None:
        return missing(_RULE_ID, "activity.trades_avg_per_hour_24h", unit="count")

    anomaly_limit = activity.trades_avg_per_hour_24h * Decimal(
        str(policy.trade_frequency.anomaly_multiplier)
    )
    absolute_limit = Decimal(policy.trade_frequency.max_trades_per_hour)
    limit = max(anomaly_limit, absolute_limit)
    observed = Decimal(activity.trades_last_1h)

    if observed > limit:
        return RuleResult(
            rule_id=_RULE_ID,
            outcome=RiskOutcome.DENY,
            reason_code="RISK_TRADE_FREQUENCY_ANOMALY",
            observed=observed,
            limit=limit,
            unit="count",
        )

    return RuleResult(
        rule_id=_RULE_ID, outcome=RiskOutcome.ALLOW, observed=observed, limit=limit, unit="count"
    )
