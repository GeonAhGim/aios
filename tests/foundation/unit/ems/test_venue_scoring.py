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


# -- D2-deepening: failure-injection, numerical performance, gate-red proof --


def test_failure_injection_extreme_fee_bps_still_ranks() -> None:
    """Failure-injection: even a pathological fee_bps (9999) does not
    crash or silently drop the candidate — it still ranks, at the bottom.

    Per DEPTH_R_EO §D2-01: 'failure-injection 1건' — inject a defect
    (fee_bps far outside realistic range) and verify the domain layer
    handles it gracefully: the candidate is ranked, not lost.
    """
    candidates = [
        VenueCandidate("NORMAL", Decimal("1"), Decimal("0.9")),
        VenueCandidate("BROKEN", Decimal("9999"), Decimal("0.9")),
    ]
    result = rank_venues(candidates)
    # Both candidates must appear in the result — none silently dropped.
    assert len(result) == 2
    # BROKEN has the same liquidity but extreme fee, so it ranks last.
    assert result[0].venue == "NORMAL"
    assert result[1].venue == "BROKEN"


@pytest.mark.perf
def test_numerical_performance_assertion_500_venues_ratio(perf_budget) -> None:
    """task-10968: raw perf_counter → PerfBudget.best_of(process_time 기반).

    ratio 단언만 유지 — budget_ms 숫자는 변경하지 않음. batch=100로
    양자화 보정 유지.
    """
    n_small = 10
    n_large = 500

    small_candidates = [
        VenueCandidate(f"venue-{i}", Decimal("1"), Decimal(f"{0.9 - i * 0.01}"))
        for i in range(n_small)
    ]
    large_candidates = [
        VenueCandidate(f"venue-{i}", Decimal("1"), Decimal(f"{0.9 - i * 0.001}"))
        for i in range(n_large)
    ]

    # Warm-up: first call may have import/cache overhead.
    rank_venues(small_candidates)
    rank_venues(large_candidates)

    # batch=100: process_time() Windows 15.6ms 틱 양자화 오차를 batch로
    # 나누어 줄인다(task-7253). best_of-N(min)으로 transient 노이즈 제거.
    small_sample = perf_budget.best_of(
        lambda: [rank_venues(small_candidates) for _ in range(100)],
        n=5,
        warmup=1,
        batch=1,
    )
    large_sample = perf_budget.best_of(
        lambda: [rank_venues(large_candidates) for _ in range(100)],
        n=5,
        warmup=1,
        batch=1,
    )

    # cpu_ms는 100회 호출 총시간(best_of가 min을 찾음).
    # ratio 계산: large/small 비율이 150배 미만이어야 선형 스케일링 유지.
    ratio = large_sample.cpu_ms / small_sample.cpu_ms if small_sample.cpu_ms > 0 else 0
    assert ratio < 150, (
        f"Performance regression: {n_large} venues took {ratio:.1f}× "
        f"the time of {n_small} venues "
        f"(small={small_sample.cpu_ms:.3f}ms, large={large_sample.cpu_ms:.3f}ms)"
    )


def test_gate_red_proof_composite_bypass_turns_red() -> None:
    """Gate-red reproduction: patch `_composite_score` to return 0 for
    all candidates (bypassing the fee/liquidity computation), then
    verify that a fee-sensitive ranking no longer produces the expected
    order — proving the original test was enforcing a real invariant,
    not a no-op assertion.

    Per DEPTH_R_EO §D2-01: '게이트 적색 재현 1건' — the test proves
    the gate was not a no-op by showing that removing the check
    causes the test suite to turn red.
    """
    from unittest.mock import patch

    candidates_fee = [
        VenueCandidate("LOW_FEE", Decimal("1"), Decimal("0.5")),
        VenueCandidate("HIGH_FEE", Decimal("100"), Decimal("0.5")),
    ]
    # Without bypass: LOW_FEE should win (lower fee = higher score).
    result_normal = rank_venues(candidates_fee)
    assert result_normal[0].venue == "LOW_FEE"

    # With the bypass, LOW_FEE and HIGH_FEE both get score 0,
    # so tie-break by venue code: HIGH_FEE < LOW_FEE alphabetically.
    # This proves the bypass removed the fee discrimination.
    with patch(
        "src.foundation.ems.domain.route.venue_scoring._composite_score",
        return_value=Decimal("0"),
    ):
        result_bypassed = rank_venues(candidates_fee)
        # With composite_score bypassed, both get 0 score,
        # tie-break by venue code ascending → HIGH_FEE wins.
        assert result_bypassed[0].venue == "HIGH_FEE", (
            "Bypass succeeded — fee discrimination removed, gate was bypassed"
        )
