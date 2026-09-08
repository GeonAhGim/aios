"""EM-4 -- venue fee-tier scoring (pure).

Spec: docs/specs/L4_ems_routing_algos_and_tca_v1.0.md §2 module table
(`domain/route/fee_model.py`), §9 EM-4.

Venue fee schedules are volume tiers keyed off trailing 30-day traded
notional -- the more a fund has traded on a venue in the last 30 days,
the lower its maker/taker bps. This module only answers "which tier
applies" and "what bps does that tier charge"; it does not fetch a
venue's schedule or a fund's trailing volume (I/O), and it does not
combine fee cost with spread/depth/fill-rate into a single venue score
(that synthesis is EM-5's `venue_scoring.py`, per the 2026-09-08 leaf
decision). The tier schedule itself is a caller-supplied argument, not a
new config file here -- which real source (BR-9 exchange matrix vs.
DC-27 source_contract) feeds it is settled by EM-6's wiring leaf.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

Liquidity = Literal["MAKER", "TAKER"]


@dataclass(frozen=True)
class FeeTier:
    """One volume-tier row of a venue's fee schedule.

    `min_30d_volume` is the inclusive lower bound of trailing 30-day
    traded notional required to qualify for this tier -- a fund whose
    trailing volume lands exactly on the boundary qualifies for the
    tier it just reached (`>=`, not `>`), matching how exchanges publish
    published volume-tier schedules.
    """

    min_30d_volume: Decimal
    maker_bps: Decimal
    taker_bps: Decimal

    def __post_init__(self) -> None:
        if self.min_30d_volume < 0:
            raise ValueError("FeeTier.min_30d_volume must be >= 0.")
        if self.maker_bps < 0 or self.taker_bps < 0:
            raise ValueError(
                "FeeTier.maker_bps/taker_bps must be >= 0 (rebate tiers are not modeled)."
            )


def select_fee_tier(tiers: Sequence[FeeTier], trailing_30d_volume: Decimal) -> FeeTier:
    """Pick the highest tier the trailing 30-day volume qualifies for.

    `tiers` must be sorted ascending by `min_30d_volume`, contain no
    duplicate thresholds, and start at `min_30d_volume == 0` -- a
    malformed schedule is a caller/config bug and fails closed here
    rather than silently guessing an ordering or defaulting to a tier.
    """
    if not tiers:
        raise ValueError("select_fee_tier: tiers must not be empty.")
    if trailing_30d_volume < 0:
        raise ValueError("select_fee_tier: trailing_30d_volume must be >= 0.")

    thresholds = [tier.min_30d_volume for tier in tiers]
    if thresholds != sorted(thresholds):
        raise ValueError("select_fee_tier: tiers must be sorted ascending by min_30d_volume.")
    if len(set(thresholds)) != len(thresholds):
        raise ValueError(
            "select_fee_tier: tiers must not have duplicate min_30d_volume thresholds."
        )
    if thresholds[0] != 0:
        raise ValueError(
            "select_fee_tier: the lowest tier must start at min_30d_volume == 0 "
            "(every non-negative volume must resolve to some tier)."
        )

    selected = tiers[0]
    for tier in tiers:
        if tier.min_30d_volume > trailing_30d_volume:
            break
        selected = tier
    return selected


def fee_bps_for_liquidity(tier: FeeTier, liquidity: Liquidity) -> Decimal:
    """Read the tier's bps for the given maker/taker flag."""
    return tier.maker_bps if liquidity == "MAKER" else tier.taker_bps


def expected_fee_bps(
    tiers: Sequence[FeeTier],
    trailing_30d_volume: Decimal,
    liquidity: Liquidity,
) -> Decimal:
    """Combinator: select the qualifying tier, then read its maker/taker bps."""
    tier = select_fee_tier(tiers, trailing_30d_volume)
    return fee_bps_for_liquidity(tier, liquidity)
