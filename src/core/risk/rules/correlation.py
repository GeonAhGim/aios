"""L4_risk_and_safety_v1.0.md#2.1, §9 R-11 — 상관 노출 판정.

risk_stats(R-18~R-20, 5ec19fb)가 계산한 `StatsInputs.correlated_exposure_pct`/
`max_correlation`을 비교만 한다. `missing_pairs`가 비어있지 않으면 다른 값과
무관하게 무조건 DENY한다 — 레거시 `correlation_with()`가 미지 페어를 0.0(무상관)
으로 암묵 치환해 통과시키던 결함(R3 감사 지적, decision 각주)을 반복하지 않는다.
순수(I/O 금지), 상한 70줄.
"""
from __future__ import annotations

from decimal import Decimal

from src.core.loader.risk_policy_loader import RiskPolicy
from src.core.risk.decision import RiskOutcome, RuleResult
from src.core.risk.inputs import RiskInputs
from src.core.risk.rules.base import missing, pct

_RULE_ID = "correlation"


def correlation(inputs: RiskInputs, policy: RiskPolicy) -> RuleResult:
    stats = inputs.stats

    if stats.missing_pairs:
        return missing(_RULE_ID, "stats.missing_pairs")
    if stats.correlated_exposure_pct is None:
        return missing(_RULE_ID, "stats.correlated_exposure_pct")
    if stats.max_correlation is None:
        return missing(_RULE_ID, "stats.max_correlation", unit="x")

    observed = pct(stats.correlated_exposure_pct)
    limit = pct(Decimal(str(policy.correlation_risk.aggregate_exposure_max_pct)))
    over_threshold = stats.max_correlation > policy.correlation_risk.threshold
    if over_threshold and observed > limit:
        return RuleResult(
            rule_id=_RULE_ID,
            outcome=RiskOutcome.DENY,
            reason_code="RISK_CORRELATION_EXPOSURE_EXCEEDED",
            observed=observed,
            limit=limit,
            unit="pct",
        )

    return RuleResult(
        rule_id=_RULE_ID, outcome=RiskOutcome.ALLOW, observed=observed, limit=limit, unit="pct"
    )
