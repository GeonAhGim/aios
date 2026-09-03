"""L4_risk_and_safety_v1.0.md#2.1, §9 R-09 — 전략 배분 상한(분모 교정).

레거시 `engine.py`는 `allocated_capital/available_balance`로 나눴다
(FD-16.1 원문 그대로). §2.1은 분모를 `total_equity`로 교정한다 —
`available_balance`는 미체결 주문 등으로 변동해 같은 배분이 시점에 따라
다른 판정을 받을 수 있기 때문이다. 상한 선택은
`services.capital_allocation.allocation_cap_pct`(순수, `certified_badge`에
따라 unverified/certified 상한을 고른다)를 재정의하지 않고 그대로
위임한다. 입력 결손은 base.missing()으로 DENY(I2).
"""
from __future__ import annotations

from decimal import Decimal

from src.core.loader.risk_policy_loader import RiskPolicy
from src.core.risk.decision import RiskOutcome, RuleResult
from src.core.risk.inputs import RiskInputs
from src.core.risk.rules.base import missing, pct
from src.services.capital_allocation import allocation_cap_pct

RULE_ID = "strategy_allocation"
_ZERO = Decimal("0")


def check(inputs: RiskInputs, policy: RiskPolicy) -> RuleResult:
    certified_badge = inputs.certified_badge
    allocated_capital = inputs.allocated_capital
    total_equity = inputs.equity.total_equity

    if certified_badge is None:
        return missing(RULE_ID, "certified_badge")
    if allocated_capital is None:
        return missing(RULE_ID, "allocated_capital")
    if total_equity is None or total_equity <= _ZERO:
        return missing(RULE_ID, "equity.total_equity")

    cap_pct = pct(allocation_cap_pct(certified_badge, policy.strategy_allocation))
    requested_pct = pct((allocated_capital / total_equity) * Decimal("100"))

    if requested_pct > cap_pct:
        return RuleResult(
            rule_id=RULE_ID,
            outcome=RiskOutcome.DENY,
            reason_code="RISK_STRATEGY_ALLOCATION_EXCEEDED",
            observed=requested_pct,
            limit=cap_pct,
            unit="pct",
        )
    return RuleResult(
        rule_id=RULE_ID,
        outcome=RiskOutcome.ALLOW,
        observed=requested_pct,
        limit=cap_pct,
        unit="pct",
    )
