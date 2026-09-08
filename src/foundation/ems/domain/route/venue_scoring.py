"""EM-5 -- venue scoring & deterministic ranking (pure).

Spec: docs/specs/L4_ems_routing_algos_and_tca_v1.0.md §2 module table
(`domain/route/venue_scoring.py`), §9 EM-5.

Combines EM-4's `fee_model.expected_fee_bps` and
`liquidity_model.depth_absorption_score` outputs into one ranking per
venue. This module does not recompute fee tiers or book depth (that
stays EM-4's job -- see `fee_model.py`/`liquidity_model.py`) and does not
decide which venues are eligible candidates, persist reason codes, or
submit orders (EM-6's job). It only ranks whatever candidate set it is
given, using the `RouteDecision` contract from EM-1 (no new contract DTO
is introduced here).

Tie-break rule (spec §9 EM-5 DoD): candidates are ordered by composite
score descending, and ties are broken by venue code ascending
(lexicographic) -- a single, explicit rule rather than input order or
insertion order, so the winner is reproducible regardless of how the
caller assembled the candidate sequence.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from src.foundation.ems.contracts.v1 import RouteDecision

_ZERO = Decimal("0")
_ONE = Decimal("1")


@dataclass(frozen=True)
class VenueCandidate:
    """One venue's EM-4 outputs, ready for EM-5 to rank.

    `fee_bps` is `fee_model.expected_fee_bps`'s result and
    `liquidity_score` is `liquidity_model.depth_absorption_score`'s result
    (already normalized to `[0, 1]`) -- both computed upstream by EM-4,
    not re-derived here. A `liquidity_score` of `0` (no usable depth) is a
    valid, in-range candidate -- it sinks to the bottom of the ranking
    through the composite score below, it is not rejected or filtered
    here (excluding it from routing entirely is EM-6's call).
    """

    venue: str
    fee_bps: Decimal
    liquidity_score: Decimal

    def __post_init__(self) -> None:
        if not self.venue:
            raise ValueError("VenueCandidate.venue must not be empty.")
        if self.fee_bps.is_nan():
            raise ValueError("VenueCandidate.fee_bps must not be NaN.")
        if self.fee_bps < _ZERO:
            raise ValueError("VenueCandidate.fee_bps must be >= 0.")
        if self.liquidity_score.is_nan():
            raise ValueError("VenueCandidate.liquidity_score must not be NaN.")
        if not (_ZERO <= self.liquidity_score <= _ONE):
            raise ValueError("VenueCandidate.liquidity_score must be within [0, 1].")


@dataclass(frozen=True)
class VenueScoreWeights:
    """The one place scoring weights live -- EM-6 (or a test) injects a
    different tuning by constructing a new instance, rather than the
    weights being scattered as magic numbers through call sites. Higher
    `liquidity_weight` favors deep books, higher `fee_weight` penalizes
    expensive venues more aggressively.

    UNVERIFIED: `DEFAULT_VENUE_SCORE_WEIGHTS` below is a placeholder
    calibration, not a value derived from real cost data -- only the
    ranking's determinism and tie-break rule are load-bearing per this
    leaf's DoD, not the absolute scale of the composite score.
    """

    fee_weight: Decimal
    liquidity_weight: Decimal

    def __post_init__(self) -> None:
        if self.fee_weight < _ZERO or self.liquidity_weight < _ZERO:
            raise ValueError("VenueScoreWeights: weights must be >= 0.")


DEFAULT_VENUE_SCORE_WEIGHTS = VenueScoreWeights(
    fee_weight=Decimal("1"), liquidity_weight=Decimal("100")
)


def _composite_score(candidate: VenueCandidate, weights: VenueScoreWeights) -> Decimal:
    """Higher is better: liquidity rewards, fee penalizes."""
    return (
        weights.liquidity_weight * candidate.liquidity_score
        - weights.fee_weight * candidate.fee_bps
    )


def _reason_codes(
    candidate: VenueCandidate,
    rank_index: int,
    candidates: Sequence[VenueCandidate],
) -> list[str]:
    """§3 examples (`BEST_FEE`/`DEEPEST_BOOK`/`ONLY_VENUE`) for the top
    candidate; lower-ranked candidates get their 1-based rank so every
    decision still carries a non-empty, deterministic reason."""
    if len(candidates) == 1:
        return ["ONLY_VENUE"]
    if rank_index != 0:
        return [f"RANK_{rank_index + 1}"]

    min_fee = min(c.fee_bps for c in candidates)
    max_liquidity = max(c.liquidity_score for c in candidates)
    codes = []
    if candidate.fee_bps == min_fee:
        codes.append("BEST_FEE")
    if candidate.liquidity_score == max_liquidity:
        codes.append("DEEPEST_BOOK")
    if not codes:
        codes.append("BEST_SCORE")
    return codes


def rank_venues(
    candidates: Sequence[VenueCandidate],
    weights: VenueScoreWeights = DEFAULT_VENUE_SCORE_WEIGHTS,
) -> list[RouteDecision]:
    """Rank `candidates` by composite score descending, venue code
    ascending on ties. The returned list always has the same length as
    `candidates` -- no candidate is dropped, regardless of score.

    Deterministic: the result depends only on the *set* of candidates,
    never on the order they were passed in (EM-A3-style determinism,
    applied to routing rather than algo scheduling).
    """
    if not candidates:
        raise ValueError("rank_venues: candidates must not be empty.")
    venues = [c.venue for c in candidates]
    if len(set(venues)) != len(venues):
        raise ValueError("rank_venues: duplicate venue codes are not allowed.")

    ranked = sorted(
        candidates,
        key=lambda c: (-_composite_score(c, weights), c.venue),
    )

    return [
        RouteDecision(
            venue=candidate.venue,
            reason_codes=_reason_codes(candidate, rank_index, candidates),
            expected_cost_bps=candidate.fee_bps,
        )
        for rank_index, candidate in enumerate(ranked)
    ]
