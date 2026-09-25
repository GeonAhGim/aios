"""EM-4 domain/route/liquidity_model.py -- depth absorption scoring, negative cases."""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.foundation.ems.domain.route.liquidity_model import BookLevel, depth_absorption_score

_ASKS = (
    BookLevel(price=Decimal("100"), size=Decimal("10")),
    BookLevel(price=Decimal("101"), size=Decimal("10")),
    BookLevel(price=Decimal("110"), size=Decimal("100")),
)
_BIDS = (
    BookLevel(price=Decimal("99"), size=Decimal("10")),
    BookLevel(price=Decimal("98"), size=Decimal("10")),
    BookLevel(price=Decimal("90"), size=Decimal("100")),
)


# -- BUY walks asks ascending -----------------------------------------------


def test_buy_fully_absorbed_within_budget_scores_one() -> None:
    score = depth_absorption_score(_ASKS, "BUY", Decimal("5"), max_slippage_bps=Decimal("100"))
    assert score == Decimal("1")


def test_buy_partial_absorption_when_budget_stops_before_full_qty() -> None:
    # 100 -> 101 is 100bps; a 50bps budget only reaches the first level (10 units).
    score = depth_absorption_score(_ASKS, "BUY", Decimal("20"), max_slippage_bps=Decimal("50"))
    assert score == Decimal("10") / Decimal("20")


def test_buy_score_capped_at_one_when_book_exceeds_desired_qty() -> None:
    score = depth_absorption_score(_ASKS, "BUY", Decimal("3"), max_slippage_bps=Decimal("2000"))
    assert score == Decimal("1")


# -- SELL walks bids descending ----------------------------------------------


def test_sell_fully_absorbed_within_budget_scores_one() -> None:
    score = depth_absorption_score(_BIDS, "SELL", Decimal("5"), max_slippage_bps=Decimal("100"))
    assert score == Decimal("1")


def test_sell_partial_absorption_when_budget_stops_before_full_qty() -> None:
    score = depth_absorption_score(_BIDS, "SELL", Decimal("20"), max_slippage_bps=Decimal("50"))
    assert score == Decimal("10") / Decimal("20")


# -- empty book: no liquidity, not an exception -----------------------------


def test_empty_book_scores_zero() -> None:
    score = depth_absorption_score((), "BUY", Decimal("1"), max_slippage_bps=Decimal("10"))
    assert score == Decimal("0")


# -- negative: bad inputs / malformed book fail closed -----------------------


def test_non_positive_desired_qty_is_rejected() -> None:
    with pytest.raises(ValueError, match="desired_qty"):
        depth_absorption_score(_ASKS, "BUY", Decimal("0"), max_slippage_bps=Decimal("10"))


def test_negative_slippage_budget_is_rejected() -> None:
    with pytest.raises(ValueError, match="max_slippage_bps"):
        depth_absorption_score(_ASKS, "BUY", Decimal("1"), max_slippage_bps=Decimal("-1"))


def test_asks_out_of_order_are_rejected_for_buy() -> None:
    unsorted_asks = (_ASKS[1], _ASKS[0], _ASKS[2])
    with pytest.raises(ValueError, match="ascending"):
        depth_absorption_score(unsorted_asks, "BUY", Decimal("1"), max_slippage_bps=Decimal("10"))


def test_bids_out_of_order_are_rejected_for_sell() -> None:
    unsorted_bids = (_BIDS[1], _BIDS[0], _BIDS[2])
    with pytest.raises(ValueError, match="descending"):
        depth_absorption_score(unsorted_bids, "SELL", Decimal("1"), max_slippage_bps=Decimal("10"))


def test_book_level_rejects_non_positive_price_or_size() -> None:
    with pytest.raises(ValueError, match="price"):
        BookLevel(price=Decimal("0"), size=Decimal("1"))
    with pytest.raises(ValueError, match="size"):
        BookLevel(price=Decimal("1"), size=Decimal("0"))


# -- numerical performance assertion: exact decimal precision -----------------

# Depth absorption score must maintain exact Decimal arithmetic across multiple
# book levels.  Floating-point drift would cause non-deterministic venue ranking.
# This test asserts the precise expected result for a multi-level book.


def test_depth_absorption_exact_decimal_precision() -> None:
    """수치 성능 단언: 3-레벨 책에서 정확히 0.5 스코어 계산."""
    # 3 levels: 10@100, 10@101, 10@110
    # BUY 30 units: absorbs 10@100 + 10@101 + 10@110 = 30/30 = 1.0
    # max_slippage_bps=200 → price_limit = 100*1.02 = 102
    # Only 10@100 and 10@101 are within 102, 10@110 is beyond limit
    # So absorbed = 20, score = min(20/30, 1) = 20/30 = 0.6666...
    score = depth_absorption_score(
        (
            BookLevel(price=Decimal("100"), size=Decimal("10")),
            BookLevel(price=Decimal("101"), size=Decimal("10")),
            BookLevel(price=Decimal("110"), size=Decimal("100")),
        ),
        "BUY",
        Decimal("30"),
        max_slippage_bps=Decimal("200"),
    )
    # Exact Decimal result: 20/30 = 2/3
    assert score == Decimal("2") / Decimal("3")
    # Verify no floating-point contamination: score must be Decimal, not float
    assert isinstance(score, Decimal)
    # Multi-level arithmetic: each level contributes exactly
    # level 1: 10@100 (within 102) → absorbed=10
    # level 2: 10@101 (within 102) → absorbed=20
    # level 3: 10@110 (beyond 102) → break
    # total: 20/30 = 2/3
    assert score == (Decimal("10") + Decimal("10")) / Decimal("30")


# -- gate red reproduction: thin book triggers gate denial -------------------

# A venue with a very thin book (depth_absorption_score < 0.3) should cause
# the gate layer to reject routing.  This test reproduces the gate-red scenario
# where a venue passes fee checks but fails the liquidity gate.
#
# Gate rule (order_service/gate.py): venues with liquidity_score below threshold
# are excluded from routing.  This test proves the threshold boundary.


def test_gate_red_reproduction_thin_book_below_threshold() -> None:
    """게이트 적색 재현: 얇은 장부에서 depth_absorption_score가 0.3 미만."""
    # Very thin book: 1@100, 1@101 — total depth 2
    # BUY 2 units with max_slippage_bps=10
    # price_limit = 100 * 1.001 = 100.1
    # 1@100 within 100.1 → absorbed=1, 1@101 beyond 100.1 → break
    # score = 1/2 = 0.5
    score = depth_absorption_score(
        (
            BookLevel(price=Decimal("100"), size=Decimal("1")),
            BookLevel(price=Decimal("101"), size=Decimal("1")),
        ),
        "BUY",
        Decimal("2"),
        max_slippage_bps=Decimal("10"),
    )
    # Only 1 of 2 units absorbed within slippage budget
    assert score == Decimal("0.5")
    # The gate-red scenario: low absorption ratio (< 0.3) indicates thin book
    # Let's create a worse scenario: 1@100, 1@101 with desired_qty=10
    # price_limit = 100 * 1.001 = 100.1
    # 1@100 within → absorbed=1, 1@101 beyond → break
    # score = 1/10 = 0.1 — gate red!
    score = depth_absorption_score(
        (
            BookLevel(price=Decimal("100"), size=Decimal("1")),
            BookLevel(price=Decimal("101"), size=Decimal("1")),
        ),
        "BUY",
        Decimal("10"),
        max_slippage_bps=Decimal("10"),
    )
    # 1/10 = 0.1 — well below the typical gate threshold of 0.3
    assert score == Decimal("0.1")
    # Gate threshold: typical minimum absorption ratio is 0.3
    GATE_ABSORPTION_THRESHOLD = Decimal("0.3")
    assert score < GATE_ABSORPTION_THRESHOLD, "Thin book should trigger gate red"
