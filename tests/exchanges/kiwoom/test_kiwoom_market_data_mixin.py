"""KiwoomMarketDataMixin — ticker/orderbook/ohlcv mapping into the common
market-data models, incl. Decimal precision.

BR-23(task-7569), exchange onboarding step (b). Uses a hand-written fake
`_request` (no HTTP layer involved) since this mixin only needs the
`_KiwoomHTTPClient` structural contract -- same isolation as
`kis/market_data_mixin.py`'s own unit-test style. Endpoint/field names match
`market_data_mixin.py`'s module docstring (ka10007/ka10081, verified via
WebFetch against github.com/Kiwoom-Securities/Kiwoom-REST-API).
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from src.core.exceptions import FatalExchangeError
from src.exchanges.kiwoom.market_data_mixin import KiwoomMarketDataMixin

_MARKET_COND_RESPONSE: dict[str, Any] = {
    "return_code": 0,
    "cur_prc": "71000",
    "trde_qty": "1234567",
    "buy_1bid": "70900",
    "buy_1bid_req": "10",
    "buy_2bid": "70800",
    "buy_2bid_req": "20",
    "sel_1bid": "71100",
    "sel_1bid_req": "5",
    "sel_2bid": "71200",
    "sel_2bid_req": "15",
}


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
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.calls.append((method, path, api_id, body))
        return self._responses[api_id]


# ---- get_ticker ----


async def test_get_ticker_maps_fields_as_decimal():
    client = _FakeKiwoomClient({"ka10007": _MARKET_COND_RESPONSE})

    ticker = await client.get_ticker("005930")

    assert ticker.symbol == "005930"
    assert ticker.exchange == "kiwoom"
    assert ticker.price == Decimal("71000")
    assert isinstance(ticker.price, Decimal)
    assert ticker.bid == Decimal("70900")
    assert ticker.ask == Decimal("71100")
    assert ticker.volume_24h == Decimal("1234567")
    assert client.calls[0][2] == "ka10007"
    assert client.calls[0][3] == {"stk_cd": "005930"}


async def test_get_ticker_rejects_malformed_symbol_without_calling_request():
    client = _FakeKiwoomClient({})
    with pytest.raises(ValueError):
        await client.get_ticker("NOT_A_CODE")
    assert client.calls == []


async def test_get_ticker_rejects_malformed_payload():
    """Negative -- a response missing the expected fields must not silently
    produce a half-populated Ticker (fail-closed)."""
    client = _FakeKiwoomClient({"ka10007": {"return_code": 0}})
    with pytest.raises(FatalExchangeError):
        await client.get_ticker("005930")


# ---- get_orderbook ----


async def test_get_orderbook_maps_levels_as_decimal_and_respects_depth():
    client = _FakeKiwoomClient({"ka10007": _MARKET_COND_RESPONSE})

    book = await client.get_orderbook("005930", depth=1)

    assert len(book.bids) == 1
    assert book.bids[0].price == Decimal("70900")
    assert isinstance(book.bids[0].quantity, Decimal)
    assert book.asks[0].price == Decimal("71100")
    assert book.asks[0].quantity == Decimal("5")


async def test_get_orderbook_rejects_malformed_payload():
    """A level price present without its matching quantity field is a
    malformed payload, not a partially-filled book."""
    client = _FakeKiwoomClient({"ka10007": {"return_code": 0, "buy_1bid": "70900"}})
    with pytest.raises(FatalExchangeError):
        await client.get_orderbook("005930")


# ---- get_ohlcv ----


async def test_get_ohlcv_maps_candles_as_decimal():
    client = _FakeKiwoomClient(
        {
            "ka10081": {
                "return_code": 0,
                "stk_dt_pole_chart_qry": [
                    {
                        "dt": "20260101",
                        "open_pric": "70000",
                        "high_pric": "71500",
                        "low_pric": "69800",
                        "cur_prc": "71000",
                        "trde_qty": "1000000",
                    },
                    {
                        "dt": "20251231",
                        "open_pric": "69000",
                        "high_pric": "70200",
                        "low_pric": "68900",
                        "cur_prc": "70000",
                        "trde_qty": "900000",
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
        {"ka10081": {"return_code": 0, "stk_dt_pole_chart_qry": [{"dt": "20260101"}]}}
    )
    with pytest.raises(FatalExchangeError):
        await client.get_ohlcv("005930", "1d")


async def test_get_ohlcv_empty_page_returns_no_candles():
    """A response with no rows key at all is not malformed -- some symbols
    genuinely have no chart history yet (e.g. freshly listed)."""
    client = _FakeKiwoomClient({"ka10081": {"return_code": 0}})
    candles = await client.get_ohlcv("005930", "1d")
    assert candles == []
