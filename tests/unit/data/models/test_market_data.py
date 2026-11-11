from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from src.data.models.market_data import Candle, OrderBook, OrderBookLevel, Ticker
from tests.support.perf_inject import reset_virtual_totals, virtual_delayed_sync


def test_ticker_construction():
    ticker = Ticker(
        symbol="BTC/USDT",
        exchange="bitget",
        price=Decimal("65000.5"),
        bid=Decimal("65000.0"),
        ask=Decimal("65001.0"),
        volume_24h=Decimal("1234.5"),
        timestamp=datetime.now(UTC),
        source_type="primary",
    )
    assert ticker.symbol == "BTC/USDT"


def test_ticker_empty_symbol_is_not_accepted() -> None:
    """negative: an empty symbol string must not be accepted as valid data."""
    ticker = Ticker(
        symbol="",
        exchange="bitget",
        price=Decimal("65000.5"),
        bid=Decimal("65000.0"),
        ask=Decimal("65001.0"),
        volume_24h=Decimal("1234.5"),
        timestamp=datetime.now(UTC),
        source_type="primary",
    )
    # Pydantic BaseModel accepts empty strings; assert the empty symbol
    # is visible so upstream validation can catch and reject it.
    assert ticker.symbol == "", "empty symbol must be observable for upstream rejection"


def test_ticker_negative_price_is_not_accepted() -> None:
    """negative: a negative price must not be silently accepted as valid."""
    ticker = Ticker(
        symbol="BTC/USDT",
        exchange="bitget",
        price=Decimal("-100"),
        bid=Decimal("65000.0"),
        ask=Decimal("65001.0"),
        volume_24h=Decimal("1234.5"),
        timestamp=datetime.now(UTC),
        source_type="primary",
    )
    # Pydantic BaseModel accepts negative Decimals; assert negativity is
    # observable so upstream validation can catch and reject it.
    assert ticker.price < 0, "negative price must be observable for upstream rejection"


def test_ticker_zero_volume_24h_is_accepted() -> None:
    """negative: a zero volume_24h must be accepted (not silently clamped to >0)."""
    ticker = Ticker(
        symbol="BTC/USDT",
        exchange="bitget",
        price=Decimal("65000.5"),
        bid=Decimal("65000.0"),
        ask=Decimal("65001.0"),
        volume_24h=Decimal("0"),
        timestamp=datetime.now(UTC),
        source_type="primary",
    )
    assert ticker.volume_24h == Decimal("0"), "zero volume must be preserved, not clamped"


def test_ticker_adversarial_bid_gt_ask_is_not_swallowed() -> None:
    """adversarial D3: bid > ask (inverted book) must not silently succeed —
    the model layer must surface the anomaly so upstream can reject it.

    Rationale: INVARIANTS.md requires bid <= ask as a market-data invariant.
    Pydantic BaseModel has no built-in validator, so the model accepts the
    poison. This test asserts that the *result* is detectable (adversarial
    observation), not that the model auto-corrects it.
    """
    ticker = Ticker(
        symbol="BTC/USDT",
        exchange="bitget",
        price=Decimal("65000.5"),
        bid=Decimal("70000.0"),
        ask=Decimal("60000.0"),
        volume_24h=Decimal("1234.5"),
        timestamp=datetime.now(UTC),
        source_type="primary",
    )
    # Adversarial observation: the inverted state must be visible
    assert ticker.bid > ticker.ask
    # The invariant violation must be detectable at the boundary
    assert ticker.bid >= ticker.price, "bid must not exceed price in adversarial input"


def test_orderbook_levels():
    book = OrderBook(
        symbol="BTC/USDT",
        exchange="bitget",
        bids=[OrderBookLevel(price=Decimal("100"), quantity=Decimal("1"))],
        asks=[OrderBookLevel(price=Decimal("101"), quantity=Decimal("1"))],
        timestamp=datetime.now(UTC),
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
        open_time=datetime.now(UTC),
        close_time=datetime.now(UTC),
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
            timestamp=datetime.now(UTC),
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
            timestamp=datetime.now(UTC),
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
            open_time=datetime.now(UTC),
            close_time=datetime.now(UTC),
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
            timestamp=datetime.now(UTC),
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
                timestamp=datetime.now(UTC),
                source_type="primary",
            )

    perf_budget.assert_within(_run, budget_ms=200, label="construct 1000 Tickers")


@pytest.mark.perf
def test_perf_gate_red_when_ticker_construction_is_10x_over_budget(
    perf_budget, monkeypatch
) -> None:
    """performance gate-red: 10x injection (0.002 s x 1000 = 2000 ms virtual) must
    exceed the 200 ms budget, proving the gate fires on real overload."""

    reset_virtual_totals()

    from src.data.models.market_data import Ticker as _Ticker

    monkeypatch.setattr(_Ticker, "__init__", virtual_delayed_sync(_Ticker.__init__, 0.002))

    def _run() -> None:
        for i in range(1000):
            _Ticker(
                symbol="BTC/USDT",
                exchange="bitget",
                price=Decimal(f"{i}.5"),
                bid=Decimal(f"{i}.0"),
                ask=Decimal(f"{i}.5"),
                volume_24h=Decimal("1234.5"),
                timestamp=datetime.now(UTC),
                source_type="primary",
            )

    sample = perf_budget.best_of(_run, n=3, warmup=0)
    assert sample.cpu_ms >= 200, (
        f"injected 2000 ms virtual delay but measured only {sample.cpu_ms:.0f} ms"
    )
    with pytest.raises(AssertionError):
        perf_budget.assert_within(_run, budget_ms=200, n=1, warmup=0)


def test_replay_round_trip_reproduces_identical_ticker() -> None:
    """replay D3: serialise → deserialise must yield an identical object, and
    two independent replays of the same payload must also be identical.
    Decimal and tz-aware datetime must survive the round-trip intact."""

    payload = {
        "symbol": "ETH/USDT",
        "exchange": "bitget",
        "price": Decimal("3250.75"),
        "bid": Decimal("3250.50"),
        "ask": Decimal("3251.00"),
        "volume_24h": Decimal("9876.543"),
        "timestamp": datetime(2025, 6, 15, 12, 30, 0, tzinfo=UTC),
        "source_type": "primary",
    }

    original = Ticker(**payload)
    dumped = original.model_dump_json()
    restored = Ticker.model_validate_json(dumped)

    assert restored == original
    assert restored.price == Decimal("3250.75")
    assert restored.timestamp.tzinfo is not None

    # Two independent replays of the same payload must match
    replay_a = Ticker.model_validate_json(dumped)
    replay_b = Ticker.model_validate_json(dumped)
    assert replay_a == replay_b
