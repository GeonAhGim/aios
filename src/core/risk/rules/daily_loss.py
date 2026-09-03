"""L4_risk_and_safety_v1.0.md#2.1, §9 R-05 — 일손실 warning/halt.

일일 손실률이 `halt_pct`를 넘으면 DENY(`RISK_DAILY_LOSS_HALT`, §3.4),
`warning_pct`와 `halt_pct` 사이면 ESCALATE(`RISK_DAILY_LOSS_WARN`). 이익
중이거나 손실이 `warning_pct` 이하면 ALLOW. 입력 결손은 base.missing()
으로 DENY(I2).
"""
from __future__ import annotations

from decimal import Decimal

from src.core.loader.risk_policy_loader import RiskPolicy
from src.core.risk.decision import RiskOutcome, RuleResult
from src.core.risk.inputs import RiskInputs
from src.core.risk.rules.base import missing, pct

RULE_ID = "daily_loss"
_ZERO = Decimal("0")


def check(inputs: RiskInputs, policy: RiskPolicy) -> RuleResult:
    daily_pnl_pct = inputs.equity.daily_pnl_pct
    if daily_pnl_pct is None:
        return missing(RULE_ID, "equity.daily_pnl_pct")

    loss_pct = pct(max(_ZERO, -daily_pnl_pct))
    halt_limit = pct(Decimal(str(policy.daily_loss.halt_pct)))
    warning_limit = pct(Decimal(str(policy.daily_loss.warning_pct)))

    if loss_pct > halt_limit:
        return RuleResult(
            rule_id=RULE_ID,
            outcome=RiskOutcome.DENY,
            reason_code="RISK_DAILY_LOSS_HALT",
            observed=loss_pct,
            limit=halt_limit,
            unit="pct",
        )
    if loss_pct > warning_limit:
        return RuleResult(
            rule_id=RULE_ID,
            outcome=RiskOutcome.ESCALATE,
            reason_code="RISK_DAILY_LOSS_WARN",
            observed=loss_pct,
            limit=warning_limit,
            unit="pct",
        )
    return RuleResult(
        rule_id=RULE_ID,
        outcome=RiskOutcome.ALLOW,
        observed=loss_pct,
        limit=halt_limit,
        unit="pct",
    )
