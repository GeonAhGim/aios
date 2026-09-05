"""FillModel 단위테스트 — L4-22. 슬리피지 부호·부분체결 극단·LIMIT 미교차."""
from __future__ import annotations

from decimal import Decimal

import pytest

from src.data.models.trading import OrderSide, OrderType
from src.exchanges.paper.fill_model import EmptyBookError, FillModel
from tests.unit.exchanges.paper.helpers import FixedRng, make_book, make_order

ADV = Decimal("1000")


def _model(prob: float = 0.0, min_pct: str = "10") -> FillModel:
    return FillModel(
        spread_bps=Decimal("10"),
        impact_bps_per_pct_adv=Decimal("2"),
        partial_fill_prob=prob,
        partial_min_pct=Decimal(min_pct),
    )


# ---- 슬리피지 부호 -------------------------------------------------------


def test_market_buy_fills_above_ask_with_positive_slippage() -> None:
    fills = _model().simulate(make_order(OrderSide.BUY), make_book(), ADV, FixedRng([]))
    assert len(fills) == 1
    fill = fills[0]
    # spread/2=5bps + impact 2bps × (1/1000×100=0.1%) = 5.2bps
    assert fill.slippage_bps == Decimal("5.2")
    assert fill.reference_price == Decimal("101")
    assert fill.price == Decimal("101") * (1 + Decimal("5.2") / 10000)
    assert fill.price > fill.reference_price
    assert fill.liquidity == "TAKER"


def test_market_sell_fills_below_bid_with_negative_slippage() -> None:
    fills = _model().simulate(make_order(OrderSide.SELL), make_book(), ADV, FixedRng([]))
    fill = fills[0]
    assert fill.slippage_bps == Decimal("-5.2")
    assert fill.reference_price == Decimal("100")
    assert fill.price < fill.reference_price


def test_impact_grows_with_participation() -> None:
    m = _model()
    assert m.slippage_bps(Decimal("100"), ADV) > m.slippage_bps(Decimal("1"), ADV)
    assert m.slippage_bps(Decimal("0"), ADV) == Decimal("5")  # spread/2만


def test_tick_rounding_never_favors_trader() -> None:
    # BUY: 101×1.00052=101.05252 → tick 0.1이면 101.1(올림). SELL: 99.948 → 99.9(내림).
    tick = Decimal("0.1")
    buy = _model().simulate(make_order(OrderSide.BUY), make_book(), ADV, FixedRng([]), tick=tick)
    sell = _model().simulate(make_order(OrderSide.SELL), make_book(), ADV, FixedRng([]), tick=tick)
    assert buy[0].price == Decimal("101.1")
    assert sell[0].price == Decimal("99.9")


# ---- LIMIT 교차 ----------------------------------------------------------


def test_limit_buy_below_ask_does_not_fill() -> None:
    order = make_order(OrderSide.BUY, OrderType.LIMIT, limit="100.5")
    assert _model().simulate(order, make_book(), ADV, FixedRng([])) == []


def test_limit_sell_above_bid_does_not_fill() -> None:
    order = make_order(OrderSide.SELL, OrderType.LIMIT, limit="100.5")
    assert _model().simulate(order, make_book(), ADV, FixedRng([])) == []


def test_limit_buy_crossing_fills_but_never_worse_than_limit() -> None:
    order = make_order(OrderSide.BUY, OrderType.LIMIT, limit="101.01")
    fills = _model().simulate(order, make_book(), ADV, FixedRng([]))
    assert len(fills) == 1
    # 슬리피지 적용가 101.05252 > limit → limit에 캡
    assert fills[0].price == Decimal("101.01")
    assert fills[0].slippage_bps > 0


def test_limit_sell_crossing_capped_at_limit() -> None:
    order = make_order(OrderSide.SELL, OrderType.LIMIT, limit="99.99")
    fills = _model().simulate(order, make_book(), ADV, FixedRng([]))
    assert fills[0].price == Decimal("99.99")


def test_limit_exactly_at_touch_crosses() -> None:
    order = make_order(OrderSide.BUY, OrderType.LIMIT, limit="101")
    fills = _model().simulate(order, make_book(), ADV, FixedRng([]))
    assert len(fills) == 1 and fills[0].price == Decimal("101")


def test_limit_with_empty_opposite_side_does_not_fill() -> None:
    order = make_order(OrderSide.BUY, OrderType.LIMIT, limit="200")
    assert _model().simulate(order, make_book(ask=None), ADV, FixedRng([])) == []


def test_limit_without_price_is_rejected() -> None:
    order = make_order(OrderSide.BUY, OrderType.LIMIT, limit=None)
    with pytest.raises(ValueError, match="price"):
        _model().simulate(order, make_book(), ADV, FixedRng([]))


# ---- 부분체결 극단 -------------------------------------------------------


def test_partial_prob_zero_never_partial_and_never_consumes_rng() -> None:
    rng = FixedRng([])  # 어떤 호출도 AssertionError
    fills = _model(prob=0.0).simulate(make_order(quantity="3"), make_book(), ADV, rng)
    assert fills[0].quantity == Decimal("3")
    assert rng.calls == 0


def test_partial_prob_one_always_partial_within_min_pct_bound() -> None:
    # 2번째 난수 0.0 → 정확히 min_pct(10%), 0.999 → 100% 미만
    low = _model(prob=1.0, min_pct="10").simulate(
        make_order(quantity="10"), make_book(), ADV, FixedRng([0.0, 0.0])
    )
    high = _model(prob=1.0, min_pct="10").simulate(
        make_order(quantity="10"), make_book(), ADV, FixedRng([0.0, 0.999])
    )
    assert low[0].quantity == Decimal("1")
    assert Decimal("1") < high[0].quantity < Decimal("10")


def test_partial_min_pct_100_boundary_equals_full_fill() -> None:
    fills = _model(prob=1.0, min_pct="100").simulate(
        make_order(quantity="7"), make_book(), ADV, FixedRng([0.0, 0.5])
    )
    assert fills[0].quantity == Decimal("7")


def test_partial_below_lot_yields_no_fill() -> None:
    # 10% of 0.5 = 0.05 < lot 0.1 → ROUND_DOWN 0 → 0체결(무음 fake fill 금지)
    fills = _model(prob=1.0, min_pct="10").simulate(
        make_order(quantity="0.5"), make_book(), ADV, FixedRng([0.0, 0.0]), lot=Decimal("0.1")
    )
    assert fills == []


def test_partial_applies_to_remaining_not_total() -> None:
    order = make_order(quantity="10", filled="8")
    fills = _model(prob=1.0, min_pct="50").simulate(order, make_book(), ADV, FixedRng([0.0, 0.0]))
    assert fills[0].quantity == Decimal("1")  # 잔량 2 × 50%


def test_fully_filled_order_yields_nothing() -> None:
    order = make_order(quantity="2", filled="2")
    assert _model().simulate(order, make_book(), ADV, FixedRng([])) == []


# ---- negative: 생성자·입력 검증 -------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        {"spread_bps": Decimal("-1")},
        {"impact_bps_per_pct_adv": Decimal("-1")},
        {"partial_fill_prob": 1.5},
        {"partial_fill_prob": -0.1},
        {"partial_min_pct": Decimal("0")},
        {"partial_min_pct": Decimal("101")},
    ],
)
def test_constructor_rejects_out_of_range(kwargs: dict[str, object]) -> None:
    base: dict[str, object] = {
        "spread_bps": Decimal("1"),
        "impact_bps_per_pct_adv": Decimal("1"),
        "partial_fill_prob": 0.5,
        "partial_min_pct": Decimal("10"),
    }
    base.update(kwargs)
    with pytest.raises(ValueError):
        FillModel(**base)  # type: ignore[arg-type]


def test_market_with_empty_book_raises_instead_of_silent_nofill() -> None:
    with pytest.raises(EmptyBookError):
        _model().simulate(make_order(OrderSide.BUY), make_book(ask=None), ADV, FixedRng([]))


def test_non_positive_adv_rejected() -> None:
    with pytest.raises(ValueError, match="adv"):
        _model().simulate(make_order(), make_book(), Decimal("0"), FixedRng([]))
