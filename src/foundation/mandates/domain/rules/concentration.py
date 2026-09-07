"""L4_compliance_and_regulatory_v1.0.md#9 CM-6 — single-instrument
concentration rule.

Same `RuleCheck` contract as `restricted_list.py`. Percentages are plain
`float` here, matching the existing convention in this bounded context
(`domain/models.py::MandateRevision.max_single_instrument_pct: float`,
`domain/rule_bundle.py`'s own test fixtures use `float` params too) rather
than `Decimal` — this rule is not a money calculation, it consumes a
pre-computed projected percentage from the snapshot.

Boundary: a projection exactly equal to the limit does not exceed it (only
strictly greater denies) — mirrors `domain/rules.py`'s existing
`POLICY_MAX_TOTAL_EXPOSURE`/`POLICY_MAX_SINGLE_INSTRUMENT` checks, which
also use strict `>`.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.foundation.mandates.contracts.v1 import ComplianceVerdict, RuleHit

RULE_ID = "concentration"


def _missing(field: str) -> RuleHit:
    return RuleHit(
        rule_id=RULE_ID,
        severity=ComplianceVerdict.DENY,
        message=f"required field '{field}' is missing from the input",
        evidence={"missing_field": field},
    )


def check(params: Mapping[str, Any], snapshot: Mapping[str, Any]) -> RuleHit | None:
    limit = params.get("max_single_instrument_pct")
    if limit is None:
        return _missing("params.max_single_instrument_pct")

    observed = snapshot.get("projected_instrument_pct")
    if observed is None:
        return _missing("snapshot.projected_instrument_pct")

    if observed > limit:
        return RuleHit(
            rule_id=RULE_ID,
            severity=ComplianceVerdict.DENY,
            message=(
                f"projected instrument concentration {observed}% exceeds limit {limit}%"
            ),
            evidence={"observed_pct": observed, "limit_pct": limit},
        )
    return None
