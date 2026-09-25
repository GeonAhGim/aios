"""FA-7 — allocation/domain/average_price.py: block-order cumulative average price (pure).

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-7
(§1 "Block order allocation" · §4 FA-A3).

A block order may be partially filled multiple times with different prices per fill.
This module aggregates those partial fills into a single weighted-average price and
assigns that price to the `AllocationLine` produced by `policy.py` (independent of
policy/rounding rules) — the policy sets quantities first, then this module attaches
the price. All sub_accounts must receive the same average price to avoid fairness
issues caused by execution timing (§1 "Allocate at average price across multiple
sub_accounts").

FA-A3 "average-price weighted-sum error ≤ 1 minimum unit" is verified by
`apply_average_price`: if the difference between the sum of allocated
(quantity × average price) and the original fill's total notional value
(`Σ quantity × price`) exceeds `notional_quantum`, it raises
`AllocationResidualError` — rounding errors from the policy's quantity rounding
that leak into the price side are caught here. Decimal-only arithmetic
(quantize applied exactly once during final average-price computation; floats prohibited).
"""
from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

from src.foundation.allocation.domain.policy import (
    AllocationLine,
    AllocationResidualError,
    round_to_quantum,
)

SCHEMA_VERSION: Literal["v1"] = "v1"


class PartialFill(BaseModel):
    """A single partial fill of a block order. Positive-quantity validation is
    the responsibility of `blended_average_price` (same rationale as
    [[policy.WeightTarget]] — the contract specifies shape only)."""

    quantity: Decimal
    price: Decimal
    schema_version: Literal["v1"] = SCHEMA_VERSION


class SubAccountAllocation(BaseModel):
    """Per-sub_account allocation result with average price assigned."""

    sub_account_id: UUID
    quantity: Decimal
    average_price: Decimal
    schema_version: Literal["v1"] = SCHEMA_VERSION


def blended_average_price(fills: Sequence[PartialFill], price_quantum: Decimal) -> Decimal:
    """Quantity-weighted average price across partial fills. Computes
    `Σquantity·price / Σquantity` and rounds once to `ROUND_HALF_EVEN` at
    `price_quantum` granularity."""
    if not fills:
        raise AllocationResidualError("No fills to compute average price from")
    if price_quantum <= 0:
        raise AllocationResidualError(f"price_quantum must be greater than 0: {price_quantum}")
    for f in fills:
        if f.quantity <= 0:
            raise AllocationResidualError(f"quantity must be greater than 0: {f.quantity}")
        if f.price <= 0:
            raise AllocationResidualError(f"price must be greater than 0: {f.price}")
    total_quantity = sum((f.quantity for f in fills), Decimal("0"))
    total_notional = sum((f.quantity * f.price for f in fills), Decimal("0"))
    return round_to_quantum(total_notional / total_quantity, price_quantum)


def apply_average_price(
    lines: Sequence[AllocationLine],
    average_price: Decimal,
    *,
    total_notional: Decimal,
    notional_quantum: Decimal,
) -> tuple[SubAccountAllocation, ...]:
    """Assign the same `average_price` to all allocation lines and verify
    FA-A3 (weighted-sum error ≤ 1 minimum unit)."""
    if not lines:
        raise AllocationResidualError("No allocation lines to assign average price to")
    if notional_quantum <= 0:
        raise AllocationResidualError(
            f"notional_quantum must be greater than 0: {notional_quantum}"
        )

    result = tuple(
        SubAccountAllocation(
            sub_account_id=line.sub_account_id,
            quantity=line.quantity,
            average_price=average_price,
        )
        for line in lines
    )
    weighted_sum = sum((line.quantity * average_price for line in result), Decimal("0"))
    error = abs(weighted_sum - total_notional)
    if error > notional_quantum:
        raise AllocationResidualError(
            f"Average-price weighted-sum error ({error}) exceeds minimum unit ({notional_quantum})"
        )
    return result
