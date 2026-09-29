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
) -> VenueScoreWeights:
    """Recompute venue ``fee_weight`` from cost samples, preserve ``liquidity_weight``.

    Algorithm
    ---------
    1. Group samples by ``venue``.
    2. For each venue compute ``avg_cost_ratio = mean(total_cost / notional)``.
       Raises ``ValueError`` on empty groups, zero, or negative ``notional``.
    3. Compare the highest-ratio venue to the mean ratio across all venues.
    4. Scale ``current.fee_weight`` by this calibration factor, clamped to
       ±20 % per call to limit volatility.
    5. Return a new ``VenueScoreWeights`` with the updated ``fee_weight``.

    Constraints
    -----------
    - Result ``fee_weight`` is always ``> 0`` (floored at ``Decimal("0.0001")``).
    - ``total_cost`` and ``notional`` must be positive; zero or negative raises
      ``ValueError``.
    - ``samples`` must be non-empty; empty sequence raises ``ValueError``.
    - NaN / Inf values in any numeric field raise ``ValueError``.
    """

    if not samples:
        raise ValueError("recalibrate_weights requires at least one sample")

    # --- validate --------------------------------------------------------
    for s in samples:
        tc = s.total_cost
        nt = s.notional
        if tc <= Decimal("0"):
            raise ValueError(f"total_cost must be positive, got {tc}")
        if nt <= Decimal("0"):
            raise ValueError(f"notional must be positive, got {nt}")
        if tc != tc or nt != nt:  # NaN guard
            raise ValueError("total_cost and notional must not be NaN")
        if abs(tc) == Decimal("Infinity") or abs(nt) == Decimal("Infinity"):
            raise ValueError("total_cost and notional must not be Infinity")

    # --- group by venue, compute avg cost ratio --------------------------
    _venue_ratios: list[Decimal] = []
    _samples_by_venue: dict[str, list[Decimal]] = {}
    for s in samples:
        _samples_by_venue.setdefault(s.venue, []).append(s.total_cost / s.notional)

    for _venue, _ratios in _samples_by_venue.items():
        _avg = statistics.mean(_ratios)
        if _avg <= Decimal("0") or _avg != _avg:
            raise ValueError(f"computed ratio for venue {_venue} is invalid")
        _venue_ratios.append(_avg)

    # --- calibration factor ----------------------------------------------
    _total_ratio = sum(_venue_ratios, Decimal("0"))
    _n_venues = len(_venue_ratios)
    _mean_ratio = _total_ratio / Decimal(str(_n_venues))
    _max_ratio = max(_venue_ratios)

    # If all venues have equal ratio, factor = 1 (no change).
    if _mean_ratio > Decimal("0"):
        _calibration_factor = _max_ratio / _mean_ratio
    else:
        _calibration_factor = Decimal("1")

    # Clamp calibration factor to ±20 % of current value.
    _delta = _calibration_factor - Decimal("1")
    if abs(_delta) > _MAX_WEIGHT_CHANGE:
        _delta = _MAX_WEIGHT_CHANGE if _delta > 0 else -_MAX_WEIGHT_CHANGE
    _new_fee_weight = current.fee_weight * (Decimal("1") + _delta)

    # Ensure strictly positive (floor at 0.0001).
    if _new_fee_weight <= Decimal("0"):
        _new_fee_weight = Decimal("0.0001")

    return VenueScoreWeights(
        fee_weight=_new_fee_weight,
        liquidity_weight=current.liquidity_weight,
    )
