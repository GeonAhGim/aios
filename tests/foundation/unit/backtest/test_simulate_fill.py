"""simulate_fill() 단위테스트 — 체결가/수수료/슬리피지 계산 검증."""
from datetime import datetime, timezone
from decimal import Decimal

from src.data.models.market_data import Candle
from src.data.models.trading import OrderSide
from src.foundation.backtest.application.simulate_fill import simulate_fill
from src.foundation.backtest.domain.models import CostModel

_NOW = datetime(2026, 1, 2, tzinfo=timezone.utc)


def _bar(*, open_price: str = "100") -> Candle:
    return Candle(
        symbol="BTC/USDT",
        exchange="bitget",
        timeframe="1h",
        open=Decimal(open_price),
        high=Decimal(open_price),
        low=Decimal(open_price),
        close=Decimal(open_price),
        volume=Decimal("1"),
        open_time=_NOW,
        close_time=_NOW,
    )


def test_buy_fill_pays_slippage_above_open() -> None:
    cost_model = CostModel(fee_bps=Decimal("0"), slippage_bps=Decimal("10"))  # 0.1%
    fill = simulate_fill(
        bar=_bar(open_price="100"),
        bar_index=1,
        side=OrderSide.BUY,
        quantity=Decimal("2"),
        cost_model=cost_model,
    )
    assert fill.price == Decimal("100.1")
    assert fill.slippage_cost == Decimal("0.2")  # (100.1-100) * 2
    assert fill.fee == Decimal("0")


def test_sell_fill_receives_slippage_below_open() -> None:
    cost_model = CostModel(fee_bps=Decimal("0"), slippage_bps=Decimal("10"))
    fill = simulate_fill(
        bar=_bar(open_price="100"),
        bar_index=1,
        side=OrderSide.SELL,
        quantity=Decimal("2"),
        cost_model=cost_model,
    )
    assert fill.price == Decimal("99.9")
    assert fill.slippage_cost == Decimal("0.2")


def test_fee_applied_on_effective_price_not_base_price() -> None:
    cost_model = CostModel(fee_bps=Decimal("100"), slippage_bps=Decimal("0"))  # 1%
    fill = simulate_fill(
        bar=_bar(open_price="100"),
        bar_index=0,
        side=OrderSide.BUY,
        quantity=Decimal("1"),
        cost_model=cost_model,
    )
    assert fill.price == Decimal("100")
    assert fill.fee == Decimal("1")


def test_fill_timestamp_and_symbol_come_from_bar() -> None:
    bar = _bar(open_price="50")
    fill = simulate_fill(
        bar=bar,
        bar_index=3,
        side=OrderSide.BUY,
        quantity=Decimal("1"),
        cost_model=CostModel(fee_bps=Decimal("0"), slippage_bps=Decimal("0")),
    )
    assert fill.timestamp == bar.open_time
    assert fill.symbol == bar.symbol
    assert fill.bar_index == 3
