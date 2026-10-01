from datetime import datetime, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

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


def test_ticker_invalid_price_type_raises_validation_error():
    """negative: a non-numeric price must be rejected, not silently coerced."""
    with pytest.raises(ValidationError):
        Ticker(
            symbol="BTC/USDT",
            exchange="bitget",
            price="not-a-number",
            bid=Decimal("65000.0"),
            ask=Decimal("65001.0"),
            volume_24h=Decimal("1234.5"),
            timestamp=datetime.now(timezone.utc),
            source_type="primary",
        )


def test_ticker_missing_required_field_raises_validation_error():
    """negative: omitting a required field (price) must fail construction."""
    with pytest.raises(ValidationError):
        Ticker(
            symbol="BTC/USDT",
            exchange="bitget",
            bid=Decimal("65000.0"),
            ask=Decimal("65001.0"),
            volume_24h=Decimal("1234.5"),
            timestamp=datetime.now(timezone.utc),
            source_type="primary",
        )


def test_ticker_invalid_timestamp_type_raises_validation_error():
    """negative: a non-datetime timestamp must be rejected."""
    with pytest.raises(ValidationError):
        Ticker(
            symbol="BTC/USDT",
            exchange="bitget",
            price=Decimal("65000.5"),
            bid=Decimal("65000.0"),
            ask=Decimal("65001.0"),
            volume_24h=Decimal("1234.5"),
            timestamp="not-a-timestamp",
            source_type="primary",
        )


def test_orderbook_level_invalid_quantity_type_raises_validation_error():
    """negative: OrderBookLevel.quantity must reject non-numeric values."""
    with pytest.raises(ValidationError):
        OrderBookLevel(price=Decimal("100"), quantity="not-a-number")


def test_candle_missing_required_field_raises_validation_error():
    """negative: omitting a required OHLC field (close) must fail construction."""
    with pytest.raises(ValidationError):
        Candle(
            symbol="BTC/USDT",
            exchange="bitget",
            timeframe="1h",
            open=Decimal("100"),
            high=Decimal("110"),
            low=Decimal("90"),
            volume=Decimal("50"),
            open_time=datetime.now(timezone.utc),
            close_time=datetime.now(timezone.utc),
        )


def test_ticker_upstream_validation_failure_is_not_swallowed(monkeypatch):
    """failure injection: if pydantic's own validation layer raises for a
    poisoned input, the exception must propagate to the caller unmasked —
    not be caught and hidden by the model layer."""
    import pydantic

    original_init = pydantic.BaseModel.__init__

    def poisoned_init(self, **data):
        if data.get("symbol") == "POISON":
            raise RuntimeError("simulated upstream validation failure")
        return original_init(self, **data)

    monkeypatch.setattr(pydantic.BaseModel, "__init__", poisoned_init)
    with pytest.raises(RuntimeError):
        Ticker(
            symbol="POISON",
            exchange="bitget",
            price=Decimal("65000.5"),
            bid=Decimal("65000.0"),
            ask=Decimal("65001.0"),
            volume_24h=Decimal("1234.5"),
            timestamp=datetime.now(timezone.utc),
            source_type="primary",
        )


@pytest.mark.perf
def test_perf_construct_1000_tickers(perf_budget):
    """performance: constructing 1000 Ticker instances must stay under 200 ms."""

    def _run() -> None:
        for i in range(1000):
            Ticker(
                symbol="BTC/USDT",
                exchange="bitget",
                price=Decimal(f"{i}.5"),
                bid=Decimal(f"{i}.0"),
                ask=Decimal(f"{i}.5"),
                volume_24h=Decimal("1234.5"),
                timestamp=datetime.now(timezone.utc),
                source_type="primary",
            )

    perf_budget.assert_within(_run, budget_ms=200, label="construct 1000 Tickers")
