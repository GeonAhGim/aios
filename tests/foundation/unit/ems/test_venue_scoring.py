"""EM-5 domain/route/venue_scoring.py -- determinism, tie-break, boundary, negative cases."""
from __future__ import annotations

import random
from decimal import Decimal

import pytest

from src.foundation.ems.contracts.v1 import RouteDecision
from src.foundation.ems.domain.route.venue_scoring import (
    DEFAULT_VENUE_SCORE_WEIGHTS,
    VenueCandidate,
    VenueScoreWeights,
    rank_venues,
)


def _decisions_to_venues(decisions: list[RouteDecision]) -> list[str]:
    return [d.venue for d in decisions]


# -- determinism: order-invariant ranking -----------------------------------


def test_ranking_is_invariant_under_random_input_shuffling() -> None:
    candidates = [
        VenueCandidate(venue="ALPHA", fee_bps=Decimal("5"), liquidity_score=Decimal("0.9")),
        VenueCandidate(venue="BETA", fee_bps=Decimal("2"), liquidity_score=Decimal("0.4")),
        VenueCandidate(venue="GAMMA", fee_bps=Decimal("8"), liquidity_score=Decimal("0.1")),
        VenueCandidate(venue="DELTA", fee_bps=Decimal("1"), liquidity_score=Decimal("0.6")),
    ]
    expected = _decisions_to_venues(rank_venues(candidates))

    rng = random.Random(1234)
    for _ in range(10):
        shuffled = list(candidates)
        rng.shuffle(shuffled)
        result = _decisions_to_venues(rank_venues(shuffled))
        assert result == expected


# -- tie-break: identical fee + liquidity -> venue code ascending -----------


def test_tie_break_picks_lexicographically_first_venue_code() -> None:
    candidates = [
        VenueCandidate(venue="ZETA", fee_bps=Decimal("3"), liquidity_score=Decimal("0.5")),
        VenueCandidate(venue="ACME", fee_bps=Decimal("3"), liquidity_score=Decimal("0.5")),
    ]
    result = rank_venues(candidates)
    assert result[0].venue == "ACME"
    assert result[1].venue == "ZETA"


def test_tie_break_winner_reason_codes_include_both_best_fee_and_deepest_book() -> None:
    candidates = [
        VenueCandidate(venue="ZETA", fee_bps=Decimal("3"), liquidity_score=Decimal("0.5")),
        VenueCandidate(venue="ACME", fee_bps=Decimal("3"), liquidity_score=Decimal("0.5")),
    ]
    result = rank_venues(candidates)
    assert result[0].reason_codes == ["BEST_FEE", "DEEPEST_BOOK"]


# -- boundary: zero liquidity sinks to bottom, is not excluded --------------


def test_zero_liquidity_venue_sinks_to_bottom_but_stays_in_result() -> None:
    candidates = [
        VenueCandidate(venue="ALPHA", fee_bps=Decimal("5"), liquidity_score=Decimal("0")),
        VenueCandidate(venue="BETA", fee_bps=Decimal("5"), liquidity_score=Decimal("0.3")),
        VenueCandidate(venue="GAMMA", fee_bps=Decimal("5"), liquidity_score=Decimal("0.9")),
    ]
    result = rank_venues(candidates)
    assert len(result) == len(candidates)
    assert {d.venue for d in result} == {"ALPHA", "BETA", "GAMMA"}
    assert result[-1].venue == "ALPHA"


def test_single_candidate_gets_only_venue_reason_code() -> None:
    candidates = [VenueCandidate(venue="ALPHA", fee_bps=Decimal("5"), liquidity_score=Decimal("0"))]
    result = rank_venues(candidates)
    assert result[0].reason_codes == ["ONLY_VENUE"]


# -- weights are a single injectable block, not scattered -------------------


def test_custom_weights_change_ranking_deterministically() -> None:
    candidates = [
        VenueCandidate(venue="CHEAP", fee_bps=Decimal("1"), liquidity_score=Decimal("0.1")),
        VenueCandidate(venue="DEEP", fee_bps=Decimal("9"), liquidity_score=Decimal("0.9")),
    ]
    fee_first = VenueScoreWeights(fee_weight=Decimal("100"), liquidity_weight=Decimal("1"))
    result = rank_venues(candidates, weights=fee_first)
    assert result[0].venue == "CHEAP"
    assert rank_venues(candidates, weights=DEFAULT_VENUE_SCORE_WEIGHTS)[0].venue == "DEEP"


# -- negative: malformed candidates / weights fail closed --------------------


def test_negative_fee_bps_is_rejected() -> None:
    with pytest.raises(ValueError, match="fee_bps"):
        VenueCandidate(venue="ALPHA", fee_bps=Decimal("-1"), liquidity_score=Decimal("0.5"))


def test_nan_fee_bps_is_rejected() -> None:
    with pytest.raises(ValueError, match="NaN"):
        VenueCandidate(venue="ALPHA", fee_bps=Decimal("NaN"), liquidity_score=Decimal("0.5"))


def test_liquidity_score_out_of_range_is_rejected() -> None:
    with pytest.raises(ValueError, match="liquidity_score"):
        VenueCandidate(venue="ALPHA", fee_bps=Decimal("1"), liquidity_score=Decimal("1.1"))


def test_empty_venue_code_is_rejected() -> None:
    with pytest.raises(ValueError, match="venue"):
        VenueCandidate(venue="", fee_bps=Decimal("1"), liquidity_score=Decimal("0.5"))


def test_empty_candidate_list_is_rejected() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        rank_venues([])


def test_duplicate_venue_codes_are_rejected() -> None:
    candidates = [
        VenueCandidate(venue="ALPHA", fee_bps=Decimal("1"), liquidity_score=Decimal("0.5")),
        VenueCandidate(venue="ALPHA", fee_bps=Decimal("2"), liquidity_score=Decimal("0.1")),
    ]
    with pytest.raises(ValueError, match="duplicate"):
        rank_venues(candidates)


def test_negative_weight_is_rejected() -> None:
    with pytest.raises(ValueError, match="weights"):
        VenueScoreWeights(fee_weight=Decimal("-1"), liquidity_weight=Decimal("1"))
