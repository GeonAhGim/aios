from datetime import datetime, timezone
from decimal import Decimal

from src.data.models.market_data import Candle, OrderBook, OrderBookLevel, Ticker


def test_ticker_construction():
    ticker = Ticker(
        symbol="BTC/USDT",
        exchange="bitget",
        price=Decimal("65000.5"),
        bid=Decimal("65000.0"),
        ask=Decimal("65001.0"),
        volume_24h=Decimal("1234.5"),
        timestamp=datetime.now(timezone.utc),
        source_type="primary",
    )
    assert ticker.symbol == "BTC/USDT"


def test_orderbook_levels():
    book = OrderBook(
        symbol="BTC/USDT",
        exchange="bitget",
        bids=[OrderBookLevel(price=Decimal("100"), quantity=Decimal("1"))],
        asks=[OrderBookLevel(price=Decimal("101"), quantity=Decimal("1"))],
        timestamp=datetime.now(timezone.utc),
    )
    assert book.bids[0].price < book.asks[0].price


def test_candle_ohlc():
    candle = Candle(
        symbol="BTC/USDT",
        exchange="bitget",
        timeframe="1h",
        open=Decimal("100"),
        high=Decimal("110"),
        low=Decimal("90"),
        close=Decimal("105"),
        volume=Decimal("50"),
        open_time=datetime.now(timezone.utc),
        close_time=datetime.now(timezone.utc),
    )
    assert candle.low <= candle.open <= candle.high
