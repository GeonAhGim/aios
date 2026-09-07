"""L4_compliance_and_regulatory_v1.0.md#9 CM-7 — mandate (delegation) leverage
ceiling, a pure compliance rule.

Axis note (task-2066 DoD item 3): this is **not** a reimplementation of the
risk axis's `src/core/risk/rules/leverage.py` (R-07). The two enforce
different ceilings from different authorities and neither may substitute for
the other (INVARIANTS.md I-09 — order ALLOW requires both RiskEngine *and*
Compliance to independently ALLOW):

- Risk R-07 compares the account's live `gross_leverage` against
  `RiskPolicy.leverage.default_max`, a risk-engine-owned parameter tuned for
  solvency/margin safety and re-evaluated on every order.
- This module compares the *same kind of ratio* against
  `params["max_leverage"]`, a limit that comes from the fund's approved
  `MandateRevision` (CM-1/CM-2 — an investment-restriction ceiling a client
  or governance body agreed to, changed only via the mandate amendment
  workflow with cooling-off, not by risk-engine tuning).

Because the two ceilings are independent numbers from independent owners,
this module imports nothing from `src.core.risk` — copying that rule's
threshold or logic here would silently collapse the two authorities into
one, which is exactly what CM-A5 forbids.
"""
from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from src.foundation.mandates.contracts.v1 import ComplianceVerdict, RuleHit

RULE_ID = "MANDATE_LEVERAGE_LIMIT"

_LEVERAGE_QUANTUM = Decimal("0.01")


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
    """`params["max_leverage"]` is the mandate's leverage ceiling (unit "x").

    `snapshot["projected_gross_leverage"]` is the order's post-trade gross
    leverage, precomputed by the caller the same way risk's
    `RiskInputs.exposure.gross_leverage` is assembled upstream — this rule
    does not compute it itself (pure comparison only, R-04-style split of
    concerns between assembly and rule body).

    Fail-closed: either field missing, or not a `Decimal`, denies (task-2066
    DoD item 4) rather than silently coercing a `str`/`float` that could
    carry precision loss into a limit comparison.
    """
    max_leverage = _decimal_or_none(params, "max_leverage")
    if max_leverage is None:
        return _missing_hit("max_leverage")

    projected = _decimal_or_none(snapshot, "projected_gross_leverage")
    if projected is None:
        return _missing_hit("projected_gross_leverage")

    if projected > max_leverage:
        return RuleHit(
            rule_id=RULE_ID,
            severity=ComplianceVerdict.DENY,
            message=(
                f"{RULE_ID}: projected gross leverage {projected}x exceeds "
                f"mandate ceiling {max_leverage}x"
            ),
            evidence={
                "projected_gross_leverage": str(projected),
                "max_leverage": str(max_leverage),
                "quantum": str(_LEVERAGE_QUANTUM),
            },
        )
    return None
