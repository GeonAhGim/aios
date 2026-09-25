# ratchet-allow: krx_uptick_required raises NotImplementedError — KRX uptick
# statute text (Enforcement Decree of FSCMA §208) not yet confirmed, task-3849
"""L4_compliance_and_regulatory_v1.0.md#9 CM-9 — short-sale surveillance
rule, a pure compliance rule.

Same `RuleCheck` contract as `restricted_list.py`/`position_limit.py`:
`(params, snapshot) -> RuleHit | None`. A sell order is a short sale when
its quantity exceeds the tenant's current position
(`snapshot["order_qty"] > snapshot["position_qty"]`); the excess is only
covered when `snapshot["borrow_available_qty"]` is at least the excess —
LA-25's `pos_borrow_position` (task-1752) locate data, passed in as a
snapshot field. This rule never queries a borrow desk or database itself.

KRX uptick note (task-3849/CM-9-fix, reviewer REJECT on task-3502): the
Korean short-sale price restriction (Enforcement Decree of the Financial
Investment Services and Capital Markets Act §208) was previously modeled
here as "a short sale's order price must not be below the last trade price"
whenever `params["krx_uptick_required"]` was set — a guess never checked
against the statute text (the exact tick/exception conditions, e.g. an
intraday uptick already having occurred, ETF/ELW carve-outs, were never
verified). Per CLAUDE.md §3 (unverified external facts must fail closed, not
guess), `check()` now raises `NotImplementedError` whenever
`params["krx_uptick_required"]` is set and `snapshot["venue"] == "KRX"`,
instead of applying the guessed rule. `evaluate_bundle`'s exception handling
(CM-A2) turns this into a DENY hit, so the fail-closed posture holds through
the real gate, not just in this function. A follow-up leaf must cite the
confirmed statute/KRX business-rule text before implementing this branch for
real; PM tracks that leaf separately.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from src.foundation.mandates.contracts.v1 import ComplianceVerdict, RuleHit

RULE_ID = "SHORT_SALE"


def _missing(field: str) -> RuleHit:
    return RuleHit(
        rule_id=RULE_ID,
        severity=ComplianceVerdict.DENY,
        message=f"{RULE_ID}: required field '{field}' is missing",
        evidence={"missing_field": field},
    )


def _decimal_or_none(source: Mapping[str, Any], key: str) -> Decimal | None:
    value = source.get(key)
    return value if isinstance(value, Decimal) else None


def check(params: Mapping[str, Any], snapshot: Mapping[str, Any]) -> RuleHit | None:
    """Fail-closed (I-02): any field needed to reach a verdict that is
    missing or not the expected type denies rather than silently allowing."""
    side = snapshot.get("side")
    if side is None:
        return _missing("snapshot.side")
    if side != "SELL":
        return None  # only sell orders can be short sales

    order_qty = _decimal_or_none(snapshot, "order_qty")
    if order_qty is None:
        return _missing("snapshot.order_qty")

    position_qty = _decimal_or_none(snapshot, "position_qty")
    if position_qty is None:
        return _missing("snapshot.position_qty")

    excess = order_qty - position_qty
    if excess <= 0:
        return None  # selling from an existing position, not a short sale

    borrow_available = _decimal_or_none(snapshot, "borrow_available_qty")
    if borrow_available is None:
        return _missing("snapshot.borrow_available_qty")

    if borrow_available < excess:
        return RuleHit(
            rule_id=RULE_ID,
            severity=ComplianceVerdict.DENY,
            message=(
                f"{RULE_ID}: naked short sale — {excess} shares short with only "
                f"{borrow_available} borrow-available"
            ),
            evidence={
                "order_qty": str(order_qty),
                "position_qty": str(position_qty),
                "excess_qty": str(excess),
                "borrow_available_qty": str(borrow_available),
            },
        )

    if params.get("krx_uptick_required") and snapshot.get("venue") == "KRX":
        raise NotImplementedError(
            f"{RULE_ID}: krx_uptick_required is not implemented — the KRX "
            "short-sale price restriction (Enforcement Decree of FSCMA §208) "
            "text has not been confirmed for this leaf (task-3849); refusing "
            "to apply a guessed rule instead of denying fail-closed"
        )

    return None
