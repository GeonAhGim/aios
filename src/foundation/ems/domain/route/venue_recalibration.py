"""venue_recalibration.py — TCA-driven venue weight recalibration (pure function).

Recompute ``VenueScoreWeights`` from historical ``VenueCostSample`` records so that
venues with higher average cost-to-notional ratio receive proportionally higher
``fee_weight``.  This is the first step of ADR-2026-09-09-B Decision B M2-10
(EMS venue weight placeholder → TCA-derived recalibration).

Immutable, no I/O, ``Decimal`` throughout.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from src.foundation.ems.domain.route.venue_scoring import VenueScoreWeights

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VenueCostSample:
    """One observation of realised cost at a single venue.

    ``total_cost`` is the actual commission/fee paid (positive).
    ``notional`` is the trade notional value (positive, never zero).
    """

    venue: str
    total_cost: Decimal
    notional: Decimal


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Maximum allowed weight change per recalibration call (±20 %).
# Rationale: prevents a single burst of noisy TCA data from destabilising
# venue selection; the weight drifts toward reality over multiple calls.
_MAX_WEIGHT_CHANGE = Decimal("0.20")


# ---------------------------------------------------------------------------
# Pure function
# ---------------------------------------------------------------------------


def recalibrate_weights(
    samples: Sequence[VenueCostSample],
    current: VenueScoreWeights,
) -> dict[str, VenueScoreWeights]:
    """Return independent venue weights targeting mean(cost / notional).

    Each venue's observed cost ratio is its absolute target fee weight;
    other venues cannot dilute that target. Clamp to +/-20% of the current
    weight per call and preserve liquidity_weight. The positive floor takes
    precedence when repairing a current weight already below 0.0001.

    Reject non-finite values before ordering or arithmetic. This is a pure
    calibration primitive, not a routing or order-submission entry point.
    """
    if not samples:
        raise ValueError("recalibrate_weights requires at least one sample")

    for name, value in (
        ("fee_weight", current.fee_weight),
        ("liquidity_weight", current.liquidity_weight),
    ):
        if not value.is_finite():
            raise ValueError(f"{name} must be finite")
        if value < 0:
            raise ValueError(f"{name} must be non-negative")

    samples_by_venue: dict[str, list[Decimal]] = {}
    for sample in samples:
        for name, value in (
            ("total_cost", sample.total_cost),
            ("notional", sample.notional),
        ):
            if not value.is_finite():
                raise ValueError(f"{name} must be finite")
            if value <= 0:
                raise ValueError(f"{name} must be positive")
        samples_by_venue.setdefault(sample.venue, []).append(
            sample.total_cost / sample.notional
        )

    lower = current.fee_weight * (Decimal("1") - _MAX_WEIGHT_CHANGE)
    upper = current.fee_weight * (Decimal("1") + _MAX_WEIGHT_CHANGE)
    result: dict[str, VenueScoreWeights] = {}
    for venue, ratios in samples_by_venue.items():
        average = statistics.mean(ratios)
        if not average.is_finite() or average <= 0:
            raise ValueError(f"computed ratio for venue {venue} is invalid")
        fee_weight = max(Decimal("0.0001"), min(upper, max(lower, average)))
        result[venue] = VenueScoreWeights(
            fee_weight=fee_weight,
            liquidity_weight=current.liquidity_weight,
        )
    return result
