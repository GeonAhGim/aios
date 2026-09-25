"""L4_product_experience_and_discovery_v1.0.md §2.4 UX-13 —
`domain/mirror_rules.py`: signal-copy rules (weight conversion, minimum unit,
rejection conditions, pure).

No I/O -- `application/mirror_signal.py` calls this before it ever calls the
risk/compliance gates, so requests that can already be rejected here (source
inactive, listing mismatch, zero quantity, ...) never pay for the gate I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal
from enum import Enum
from typing import Literal
from uuid import UUID

from src.foundation.follow.contracts.v1 import FollowSubscription, SizingPolicy, SourceSignal

MIN_NOTIONAL_UNIT = Decimal("0.01")
"""Minimum tradable unit (2 decimal places) -- a conversion below this is rejected."""


class MirrorRejectionReason(str, Enum):
    SOURCE_INACTIVE = "FOLLOW_SOURCE_INACTIVE"
    LISTING_MISMATCH = "FOLLOW_LISTING_MISMATCH"
    FLAT_SIGNAL = "FOLLOW_FLAT_SIGNAL"
    ZERO_QUANTITY = "FOLLOW_ZERO_QUANTITY"
    MAX_NOTIONAL_EXCEEDED = "FOLLOW_MAX_NOTIONAL_EXCEEDED"


class MirrorRejectedError(Exception):
    """The domain rules rejected this signal before any gate was called --
    the same fail-closed shape as spec §3's `UX_FOLLOW_SOURCE_INACTIVE` (409)."""

    def __init__(self, reason: MirrorRejectionReason) -> None:
        self.reason = reason
        super().__init__(reason.value)


@dataclass(frozen=True)
class MirrorOrderDraft:
    """Weight-converted draft ready for gate evaluation -- not yet an order intent."""

    subscription_id: UUID
    symbol: str
    side: Literal["BUY", "SELL"]
    notional: Decimal


def convert_signal(subscription: FollowSubscription, signal: SourceSignal) -> MirrorOrderDraft:
    """spec §2.4 "weight conversion / minimum unit / rejection conditions" -- pure function.

    Rejection order: listing mismatch -> source inactive -> FLAT signal ->
    conversion result below the minimum unit or above `max_notional`.
    """
    if signal.source_listing != subscription.source_listing:
        raise MirrorRejectedError(MirrorRejectionReason.LISTING_MISMATCH)
    if not signal.is_active:
        raise MirrorRejectedError(MirrorRejectionReason.SOURCE_INACTIVE)
    if signal.side == "FLAT":
        raise MirrorRejectedError(MirrorRejectionReason.FLAT_SIGNAL)

    if subscription.sizing_policy == SizingPolicy.MIRROR_WEIGHT:
        notional = (subscription.max_notional * signal.source_weight_pct / Decimal(100)).quantize(
            MIN_NOTIONAL_UNIT, rounding=ROUND_DOWN
        )
    elif subscription.sizing_policy == SizingPolicy.FIXED_NOTIONAL:
        notional = subscription.max_notional.quantize(MIN_NOTIONAL_UNIT, rounding=ROUND_DOWN)
    else:
        raise ValueError(f"FOLLOW_SIZING_POLICY_UNSUPPORTED: {subscription.sizing_policy!r}")

    if notional < MIN_NOTIONAL_UNIT:
        raise MirrorRejectedError(MirrorRejectionReason.ZERO_QUANTITY)
    if notional > subscription.max_notional:
        raise MirrorRejectedError(MirrorRejectionReason.MAX_NOTIONAL_EXCEEDED)

    return MirrorOrderDraft(
        subscription_id=subscription.id,
        symbol=signal.symbol,
        side=signal.side,
        notional=notional,
    )
