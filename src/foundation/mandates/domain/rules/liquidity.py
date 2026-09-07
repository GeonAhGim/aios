"""L4_compliance_and_regulatory_v1.0.md#9 CM-7 — mandate liquidity ceiling, a
pure compliance rule.

Denies a single order whose notional exceeds `params["max_pct_of_adv"]`
percent of the instrument's average daily traded value (ADV). This protects
against market-impact/illiquid-execution risk that a fund's mandate caps
independently of price-based limits — it is unrelated to the risk axis's
position-sizing rules and does not import from `src.core.risk` (same
authority-separation reasoning as `leverage.py` in this package).
"""
from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from src.foundation.mandates.contracts.v1 import ComplianceVerdict, RuleHit

RULE_ID = "MANDATE_LIQUIDITY_LIMIT"

_PCT_QUANTUM = Decimal("0.01")
_HUNDRED = Decimal("100")
_ZERO = Decimal("0")


def _missing_hit(field: str) -> RuleHit:
    return RuleHit(
        rule_id=RULE_ID,
        severity=ComplianceVerdict.DENY,
        message=f"{RULE_ID}: missing or non-Decimal field '{field}'",
        evidence={"missing_field": field},
    )


def _decimal_or_none(source: Mapping[str, Any], key: str) -> Decimal | None:
    value = source.get(key)
    return value if isinstance(value, Decimal) else None


def check(params: Mapping[str, Any], snapshot: Mapping[str, Any]) -> RuleHit | None:
    """`params["max_pct_of_adv"]` bounds `order_notional / average_daily_traded_value * 100`.

    Fail-closed (task-2066 DoD item 4): missing/non-`Decimal` fields deny,
    and so does a non-positive `average_daily_traded_value` — a zero or
    negative ADV means liquidity cannot be verified, which must never be
    read as "no limit" (I-09-style default-deny, not default-allow).
    """
    max_pct = _decimal_or_none(params, "max_pct_of_adv")
    if max_pct is None:
        return _missing_hit("max_pct_of_adv")

    order_notional = _decimal_or_none(snapshot, "order_notional")
    if order_notional is None:
        return _missing_hit("order_notional")

    adv = _decimal_or_none(snapshot, "average_daily_traded_value")
    if adv is None:
        return _missing_hit("average_daily_traded_value")
    if adv <= _ZERO:
        return RuleHit(
            rule_id=RULE_ID,
            severity=ComplianceVerdict.DENY,
            message=f"{RULE_ID}: non-positive average_daily_traded_value {adv}",
            evidence={"average_daily_traded_value": str(adv)},
        )

    observed_pct = (order_notional / adv) * _HUNDRED

    if observed_pct > max_pct:
        return RuleHit(
            rule_id=RULE_ID,
            severity=ComplianceVerdict.DENY,
            message=(
                f"{RULE_ID}: order notional {order_notional} is {observed_pct}% of "
                f"ADV {adv}, exceeding the mandate's {max_pct}% limit"
            ),
            evidence={
                "order_notional": str(order_notional),
                "average_daily_traded_value": str(adv),
                "observed_pct_of_adv": str(observed_pct),
                "max_pct_of_adv": str(max_pct),
            },
        )
    return None
