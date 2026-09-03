"""L4_risk_and_safety_v1.0.md#2.1, §9 R-06 — peak 대비 낙폭 warning/hard_stop.

낙폭률이 `hard_stop_pct`를 넘으면 DENY(`RISK_MDD_HARD_STOP`, §3.4),
`warning_pct`와 `hard_stop_pct` 사이면 ESCALATE(`RISK_MDD_WARN`).
`warning_pct` 이하면 ALLOW. 입력 결손은 base.missing()으로 DENY(I2).
"""
from __future__ import annotations

from decimal import Decimal

from src.core.loader.risk_policy_loader import RiskPolicy
from src.core.risk.decision import RiskOutcome, RuleResult
from src.core.risk.inputs import RiskInputs
from src.core.risk.rules.base import missing, pct

RULE_ID = "max_drawdown"


def check(inputs: RiskInputs, policy: RiskPolicy) -> RuleResult:
    drawdown_pct = inputs.equity.drawdown_pct
    if drawdown_pct is None:
        return missing(RULE_ID, "equity.drawdown_pct")

    observed = pct(drawdown_pct)
    hard_stop_limit = pct(Decimal(str(policy.max_drawdown.hard_stop_pct)))
    warning_limit = pct(Decimal(str(policy.max_drawdown.warning_pct)))

    if observed > hard_stop_limit:
        return RuleResult(
            rule_id=RULE_ID,
            outcome=RiskOutcome.DENY,
            reason_code="RISK_MDD_HARD_STOP",
            observed=observed,
            limit=hard_stop_limit,
            unit="pct",
        )
    if observed > warning_limit:
        return RuleResult(
            rule_id=RULE_ID,
            outcome=RiskOutcome.ESCALATE,
            reason_code="RISK_MDD_WARN",
            observed=observed,
            limit=warning_limit,
            unit="pct",
        )
    return RuleResult(
        rule_id=RULE_ID,
        outcome=RiskOutcome.ALLOW,
        observed=observed,
        limit=hard_stop_limit,
        unit="pct",
    )
