"""L4_compliance_and_regulatory_v1.0.md#9 CM-7 — mandate per-instrument
position ceiling, a pure compliance rule.

Denies an order whose projected post-trade position notional in a single
instrument exceeds `params["max_position_notional"]`, the mandate's absolute
position cap for that instrument. This is a delegation-scoped limit (set via
the mandate amendment workflow, CM-1/CM-2) distinct from the risk axis's
percentage-of-equity concentration rule (`src/core/risk/rules/
concentration.py`, R-08) — same authority-separation reasoning as
`leverage.py` in this package, so this module does not import from
`src.core.risk`.
"""
from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from src.foundation.mandates.contracts.v1 import ComplianceVerdict, RuleHit

RULE_ID = "MANDATE_POSITION_LIMIT"

_NOTIONAL_QUANTUM = Decimal("0.01")


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
    """`params["max_position_notional"]` bounds
    `snapshot["projected_position_notional"]` (absolute, same currency).

    Fail-closed (task-2066 DoD item 4): either field missing or not a
    `Decimal` denies rather than silently coercing a `str`/`float`.
    """
    max_position = _decimal_or_none(params, "max_position_notional")
    if max_position is None:
        return _missing_hit("max_position_notional")

    projected = _decimal_or_none(snapshot, "projected_position_notional")
    if projected is None:
        return _missing_hit("projected_position_notional")

    if projected > max_position:
        return RuleHit(
            rule_id=RULE_ID,
            severity=ComplianceVerdict.DENY,
            message=(
                f"{RULE_ID}: projected position notional {projected} exceeds "
                f"mandate ceiling {max_position}"
            ),
            evidence={
                "projected_position_notional": str(projected),
                "max_position_notional": str(max_position),
                "quantum": str(_NOTIONAL_QUANTUM),
            },
        )
    return None
