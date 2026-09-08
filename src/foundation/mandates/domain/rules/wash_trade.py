"""L4_compliance_and_regulatory_v1.0.md#9 CM-9 — wash-trade surveillance
rule, a pure compliance rule.

Detects a tenant crossing its own book: an incoming order and one of the
tenant's own open orders in the same instrument, opposite side, with prices
that cross (would execute against each other with no genuine counterparty).
Same `RuleCheck` contract as the rest of `domain/rules/*.py`:
`(params, snapshot) -> RuleHit | None`.

Cross-tenant opposite orders are never a wash trade — a real counterparty on
the other side of a trade is exactly what a market is for — so this rule
only ever compares open orders whose `tenant_id` matches the incoming
order's `tenant_id`.
"""
from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from src.foundation.mandates.contracts.v1 import ComplianceVerdict, RuleHit

RULE_ID = "WASH_TRADE"

_SIDES = ("BUY", "SELL")


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


def _crosses(
    incoming_side: str, incoming_price: Decimal, open_side: str, open_price: Decimal
) -> bool:
    if incoming_side == "SELL" and open_side == "BUY":
        return incoming_price <= open_price
    if incoming_side == "BUY" and open_side == "SELL":
        return incoming_price >= open_price
    return False


def check(_params: Mapping[str, Any], snapshot: Mapping[str, Any]) -> RuleHit | None:
    """Fail-closed (I-02): any field needed to reach a verdict that is
    missing denies rather than silently allowing an un-checkable order
    through. `_params` is unused — this rule has no tunable threshold, only
    the `RuleCheck` shape (`(params, snapshot) -> RuleHit | None`)."""
    tenant_id = snapshot.get("tenant_id")
    if tenant_id is None:
        return _missing("snapshot.tenant_id")

    instrument = snapshot.get("instrument")
    if instrument is None:
        return _missing("snapshot.instrument")

    side = snapshot.get("side")
    if side not in _SIDES:
        return _missing("snapshot.side")

    order_price = _decimal_or_none(snapshot, "order_price")
    if order_price is None:
        return _missing("snapshot.order_price")

    open_orders = snapshot.get("open_orders")
    if open_orders is None:
        return _missing("snapshot.open_orders")

    for open_order in open_orders:
        if open_order.get("tenant_id") != tenant_id:
            continue
        if open_order.get("instrument") != instrument:
            continue
        open_side = open_order.get("side")
        if open_side == side:
            continue
        open_price = _decimal_or_none(open_order, "price")
        if open_price is None:
            continue
        if _crosses(side, order_price, open_side, open_price):
            return RuleHit(
                rule_id=RULE_ID,
                severity=ComplianceVerdict.DENY,
                message=(
                    f"{RULE_ID}: order crosses own open order in {instrument} — "
                    f"incoming {side} {order_price} vs open {open_side} {open_price}"
                ),
                evidence={
                    "tenant_id": str(tenant_id),
                    "instrument": str(instrument),
                    "incoming_side": side,
                    "incoming_price": str(order_price),
                    "open_side": open_side,
                    "open_price": str(open_price),
                },
            )
    return None
