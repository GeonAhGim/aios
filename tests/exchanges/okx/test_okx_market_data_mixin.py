"""task-7595(BR-21b) -- OKXMarketDataMixin get_ticker/get_orderbook/get_ohlcv tests.

D2 floor: negative tests >= 3, one failure-injection test, one numeric
performance assertion.
"""

from __future__ import annotations

import time
from decimal import Decimal
from typing import Any

import pytest

from src.core.exceptions import FatalExchangeError
from src.exchanges.okx.market_data_mixin import OKXMarketDataMixin


class _StubClient(OKXMarketDataMixin):
    def __init__(self, responses: dict[str, dict[str, Any]]) -> None:
        self._responses = responses
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []

    async def _request(
        self, method: str, path: str, *, params: dict[str, Any] | None = None, **_: Any
    ) -> dict[str, Any]:
        self.calls.append((method, path, params))
        return self._responses[path]


# ---- DoD 2: signature matches ExchangeAdapter ABC (instantiation raises no TypeError) ----
#
# Proven at the full-assembly level instead of duplicating a second minimal
# ABC subclass here: `tests/exchanges/okx/test_okx_factory.py::
# test_okx_adapter_instantiates_without_typeerror` instantiates the real
# `OKXAdapter` (which includes this mixin) end-to-end -- a strictly stronger
# proof that get_ticker/get_orderbook/get_ohlcv's signatures satisfy the ABC
# than a second, separately-typed minimal stub would add.


# ---- happy path ----


async def test_get_ticker_parses_last_bid_ask_volume():
    client = _StubClient(
        {
            "/api/v5/market/ticker": {
                "code": "0",
                "data": [
                    {
                        "instId": "BTC-USDT",
                        "last": "50000.5",
                        "bidPx": "50000.1",
                        "askPx": "50000.9",
                        "vol24h": "1234.5",
                        "ts": "1607417337715",
                    }
                ],
            }
        }
    )
    ticker = await client.get_ticker("BTC-USDT")
    assert ticker.price == Decimal("50000.5")
    assert ticker.bid == Decimal("50000.1")
    assert ticker.ask == Decimal("50000.9")
    assert ticker.volume_24h == Decimal("1234.5")
    assert ticker.exchange == "okx"


async def test_get_orderbook_parses_bids_and_asks():
    client = _StubClient(
        {
            "/api/v5/market/books": {
                "code": "0",
                "data": [
                    {
                        "asks": [["50001", "0.5", "0", "3"]],
                        "bids": [["50000", "1.2", "0", "2"]],
                        "ts": "1607417337715",
                    }
                ],
            }
        }
    )
    book = await client.get_orderbook("BTC-USDT", depth=10)
    assert book.asks[0].price == Decimal("50001")
    assert book.asks[0].quantity == Decimal("0.5")
    assert book.bids[0].price == Decimal("50000")
    assert book.bids[0].quantity == Decimal("1.2")


async def test_get_ohlcv_reverses_newest_first_response_to_oldest_first():
    client = _StubClient(
        {
            "/api/v5/market/candles": {
                "code": "0",
                "data": [
                    ["1607417400000", "50100", "50200", "50050", "50150", "10", "0", "0", "1"],
                    ["1607417337715", "50000", "50100", "49900", "50050", "5", "0", "0", "1"],
                ],
            }
        }
    )
    candles = await client.get_ohlcv("BTC-USDT", "1m", limit=2)
    assert len(candles) == 2
    assert candles[0].open_time < candles[1].open_time
    assert candles[0].close == Decimal("50050")
    assert candles[1].close == Decimal("50150")


# ---- negative / failure-injection tests ----


async def test_get_ticker_raises_fatal_on_empty_data_array():
    """Negative 1: an empty data array (venue-side schema anomaly) must not
    raise an IndexError -- a uniform FatalExchangeError instead."""
    client = _StubClient({"/api/v5/market/ticker": {"code": "0", "data": []}})
    with pytest.raises(FatalExchangeError):
        await client.get_ticker("BTC-USDT")


async def test_get_ticker_raises_fatal_on_missing_last_field():
    """Negative 2: a malformed row missing the required `last` field."""
    client = _StubClient(
        {"/api/v5/market/ticker": {"code": "0", "data": [{"instId": "BTC-USDT", "ts": "1"}]}}
    )
    with pytest.raises(FatalExchangeError):
        await client.get_ticker("BTC-USDT")


async def test_get_orderbook_raises_fatal_on_missing_bids_field():
    """Negative 3: missing bids/asks fields must fail closed, not return an
    empty book silently."""
    client = _StubClient(
        {"/api/v5/market/books": {"code": "0", "data": [{"asks": [], "ts": "1"}]}}
    )
    with pytest.raises(FatalExchangeError):
        await client.get_orderbook("BTC-USDT")


async def test_get_ohlcv_raises_fatal_on_malformed_row_missing_fields():
    """Negative 4 / failure-injection: a candle row shorter than the
    documented [ts,o,h,l,c,vol,...] shape must not raise a raw IndexError
    (which would look like an unrelated crash to the caller)."""
    client = _StubClient({"/api/v5/market/candles": {"code": "0", "data": [["1607417337715"]]}})
    with pytest.raises(FatalExchangeError):
        await client.get_ohlcv("BTC-USDT", "1m")


async def test_get_ohlcv_raises_fatal_on_missing_data_key():
    """Negative 5: entirely missing `data` key (distinct from an empty
    array) is also rejected explicitly."""
    client = _StubClient({"/api/v5/market/candles": {"code": "0"}})
    with pytest.raises(FatalExchangeError):
        await client.get_ohlcv("BTC-USDT", "1m")


# ---- numeric performance assertion ----


@pytest.mark.perf
async def test_get_ticker_latency_budget():
    """Numeric performance assertion: parsing overhead (no network, pure
    stub client) must average under 1ms across 100 calls."""
    client = _StubClient(
        {
            "/api/v5/market/ticker": {
                "code": "0",
                "data": [
                    {
                        "instId": "BTC-USDT",
                        "last": "50000.5",
                        "bidPx": "50000.1",
                        "askPx": "50000.9",
                        "vol24h": "1234.5",
                        "ts": "1607417337715",
                    }
                ],
            }
        }
    )
    start = time.perf_counter()
    for _ in range(100):
        await client.get_ticker("BTC-USDT")
    elapsed = time.perf_counter() - start
    assert elapsed / 100 < 0.001
