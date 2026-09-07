"""L4_compliance_and_regulatory_v1.0.md#9 CM-6 — restricted-list rule.

One rule = one file (spec §2.1). Implements `domain/rule_bundle.py`'s
`RuleCheck` contract: `(params, snapshot) -> RuleHit | None`. `None` means
"no hit" (allow); a raised exception would also be turned into a fail-closed
DENY by `domain/evaluator.py`, but this rule never needs to raise — every
input branch below returns an explicit `RuleHit | None` (CM-A2 fail-closed:
a missing `snapshot["symbol"]` denies rather than silently allowing an
un-checkable order through).

`params["restricted_symbols"]` membership is an exact, case-sensitive string
match — no ticker/exchange-suffix normalization (that belongs to a future
market-abuse/exclusion-list-source leaf, not this one).
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from src.foundation.mandates.contracts.v1 import ComplianceVerdict, RuleHit

RULE_ID = "restricted_list"


def _missing(field: str) -> RuleHit:
    return RuleHit(
        rule_id=RULE_ID,
        severity=ComplianceVerdict.DENY,
        message=f"required field '{field}' is missing from the input",
        evidence={"missing_field": field},
    )


def check(params: Mapping[str, Any], snapshot: Mapping[str, Any]) -> RuleHit | None:
    if "symbol" not in snapshot or snapshot["symbol"] is None:
        return _missing("snapshot.symbol")
    symbol = snapshot["symbol"]

    restricted_symbols: Iterable[str] = params.get("restricted_symbols", ())
    if symbol in restricted_symbols:
        return RuleHit(
            rule_id=RULE_ID,
            severity=ComplianceVerdict.DENY,
            message=f"symbol '{symbol}' is on the restricted list",
            evidence={"symbol": symbol},
        )
    return None
