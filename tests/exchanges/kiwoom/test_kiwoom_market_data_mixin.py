"""KiwoomMarketDataMixin — ticker/orderbook/ohlcv mapping into the common
market-data models, incl. Decimal precision.

BR-23(task-7569), exchange onboarding step (b). Uses a hand-written fake
`_request` (no HTTP layer involved) since this mixin only needs the
`_KiwoomHTTPClient` structural contract -- same isolation as
`kis/market_data_mixin.py`'s own unit-test style.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from src.core.exceptions import FatalExchangeError
from src.exchanges.kiwoom.market_data_mixin import KiwoomMarketDataMixin


class _FakeKiwoomClient(KiwoomMarketDataMixin):
    def __init__(self, responses: dict[str, dict[str, Any]]) -> None:
        self._responses = responses
        self.calls: list[tuple[str, str, str, dict[str, Any] | None]] = []

    async def _request(
        self,
        method: str,
        path: str,
        api_id: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.calls.append((method, path, api_id, params))
        return self._responses[api_id]


# ---- get_ticker ----


async def test_get_ticker_maps_fields_as_decimal():
    client = _FakeKiwoomClient(
        {
            "KW_TICKER": {
                "return_code": 0,
                "output": {
                    "current_price": "71000",
                    "bid_price": "70900",
                    "ask_price": "71100",
                    "volume": "1234567",
                },
            }
        }
    )

    ticker = await client.get_ticker("005930")

    assert ticker.symbol == "005930"
    assert ticker.exchange == "kiwoom"
    assert ticker.price == Decimal("71000")
    assert isinstance(ticker.price, Decimal)
    assert ticker.bid == Decimal("70900")
    assert ticker.ask == Decimal("71100")
    assert ticker.volume_24h == Decimal("1234567")
    assert client.calls[0][2] == "KW_TICKER"


async def test_get_ticker_rejects_malformed_symbol_without_calling_request():
    client = _FakeKiwoomClient({})
    with pytest.raises(ValueError):
        await client.get_ticker("NOT_A_CODE")
    assert client.calls == []


async def test_get_ticker_rejects_malformed_payload():
    """Negative -- a response missing the expected `output` fields must not
    silently produce a half-populated Ticker (fail-closed)."""
    client = _FakeKiwoomClient({"KW_TICKER": {"return_code": 0, "output": {}}})
    with pytest.raises(FatalExchangeError):
        await client.get_ticker("005930")


# ---- get_orderbook ----


async def test_get_orderbook_maps_levels_as_decimal_and_respects_depth():
    client = _FakeKiwoomClient(
        {
            "KW_ORDERBOOK": {
                "return_code": 0,
                "output": {
                    "bids": [
                        {"price": "70900", "quantity": "10"},
                        {"price": "70800", "quantity": "20"},
                        {"price": "70700", "quantity": "30"},
                    ],
                    "asks": [
                        {"price": "71000", "quantity": "5"},
                        {"price": "71100", "quantity": "15"},
                    ],
                },
            }
        }
    )

    book = await client.get_orderbook("005930", depth=2)

    assert len(book.bids) == 2
    assert book.bids[0].price == Decimal("70900")
    assert isinstance(book.bids[0].quantity, Decimal)
    assert book.asks[0].price == Decimal("71000")
    assert book.asks[0].quantity == Decimal("5")


async def test_get_orderbook_rejects_malformed_payload():
    client = _FakeKiwoomClient({"KW_ORDERBOOK": {"return_code": 0, "output": {"bids": [{}]}}})
    with pytest.raises(FatalExchangeError):
        await client.get_orderbook("005930")


# ---- get_ohlcv ----


async def test_get_ohlcv_maps_candles_as_decimal():
    client = _FakeKiwoomClient(
        {
            "KW_OHLCV_DAY": {
                "return_code": 0,
                "output": [
                    {
                        "date": "20260101",
                        "open": "70000",
                        "high": "71500",
                        "low": "69800",
                        "close": "71000",
                        "volume": "1000000",
                    },
                    {
                        "date": "20251231",
                        "open": "69000",
                        "high": "70200",
                        "low": "68900",
                        "close": "70000",
                        "volume": "900000",
                    },
                ],
            }
        }
    )

    candles = await client.get_ohlcv("005930", "1d", limit=1)

    assert len(candles) == 1  # limit truncates the response
    candle = candles[0]
    assert candle.timeframe == "1d"
    assert candle.open == Decimal("70000")
    assert candle.close == Decimal("71000")
    assert isinstance(candle.volume, Decimal)
    assert candle.open_time.year == 2026
    assert candle.open_time.month == 1
    assert candle.open_time.day == 1


async def test_get_ohlcv_rejects_unsupported_timeframe():
    client = _FakeKiwoomClient({})
    with pytest.raises(ValueError):
        await client.get_ohlcv("005930", "5m")
    assert client.calls == []


async def test_get_ohlcv_rejects_malformed_payload():
    client = _FakeKiwoomClient(
        {"KW_OHLCV_DAY": {"return_code": 0, "output": [{"date": "20260101"}]}}
    )
    with pytest.raises(FatalExchangeError):
        await client.get_ohlcv("005930", "1d")
