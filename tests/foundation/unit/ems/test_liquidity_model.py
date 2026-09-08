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
