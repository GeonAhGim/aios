"""L4_risk_and_safety_v1.0.md#2.1, §9 R-11 — 포스트트레이드 VaR·ES 판정.

risk_stats(R-18~R-20, 5ec19fb)가 이미 계산한 `StatsInputs.var_pct`/`es_pct`를
비교만 한다 — 통계 자체는 여기서 재구현하지 않는다(task-1177 decision). VaR ≤
max_pct **and** ES ≤ es_max_pct를 동시에 요구하고, `bars_used`가 `min_bars`
미달이면 "계산은 됐지만 표본이 부족해 신뢰할 수 없다"를 결손과 동일하게
DENY한다(R3 fail-closed). 순수(I/O 금지), 상한 90줄.
"""
from __future__ import annotations

from decimal import Decimal

from src.core.loader.risk_policy_loader import RiskPolicy
from src.core.risk.decision import RiskOutcome, RuleResult
from src.core.risk.inputs import RiskInputs
from src.core.risk.rules.base import missing, pct

_RULE_ID = "var_es"


def var_es(inputs: RiskInputs, policy: RiskPolicy) -> RuleResult:
    stats = inputs.stats

    if stats.var_pct is None:
        return missing(_RULE_ID, "stats.var_pct")
    if stats.es_pct is None:
        return missing(_RULE_ID, "stats.es_pct")
    if stats.var_method is None:
        return missing(_RULE_ID, "stats.var_method", unit="count")
    if stats.bars_used is None or stats.bars_used < policy.var.min_bars:
        # 표본 자체가 없거나(None) min_bars 미달이면 둘 다 결손 취급(§9 R-11 DoD).
        return missing(_RULE_ID, "stats.bars_used", unit="count")

    var_limit = pct(Decimal(str(policy.var.max_pct)))
    if stats.var_pct > var_limit:
        return RuleResult(
            rule_id=_RULE_ID,
            outcome=RiskOutcome.DENY,
            reason_code="RISK_VAR_EXCEEDED",
            observed=stats.var_pct,
            limit=var_limit,
            unit="pct",
        )

    es_limit = pct(Decimal(str(policy.var.es_max_pct)))
    if stats.es_pct > es_limit:
        return RuleResult(
            rule_id=_RULE_ID,
            outcome=RiskOutcome.DENY,
            reason_code="RISK_ES_EXCEEDED",
            observed=stats.es_pct,
            limit=es_limit,
            unit="pct",
        )

    return RuleResult(
        rule_id=_RULE_ID,
        outcome=RiskOutcome.ALLOW,
        observed=stats.var_pct,
        limit=var_limit,
        unit="pct",
    )
