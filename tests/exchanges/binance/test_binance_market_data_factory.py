"""task-7600 -- market data mixin, venue_profile wiring, BR-9 registration."""

from __future__ import annotations

import inspect
from decimal import Decimal

import httpx
import pytest

from src.core.exceptions import FatalExchangeError
from src.exchanges.binance import factory as bfactory
from src.exchanges.binance.factory import BinanceAdapter
from src.exchanges.binance.market_data_mixin import BinanceMarketDataMixin
from src.exchanges.binance.venue_profile import BINANCE_SPOT_PROFILE
from src.exchanges.common.adapter import ExchangeAdapter
from src.exchanges.factory import build_adapter, register_exchange_adapter_factory


def _adapter(payload):
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload)),
        base_url="https://x.test",
    )
    return BinanceAdapter("k", "s", http_client=http)


@pytest.mark.parametrize("name", ["get_ticker", "get_orderbook", "get_ohlcv"])
def test_signature_matches_abc(name):
    assert inspect.signature(getattr(BinanceMarketDataMixin, name)).parameters.keys() == (
        inspect.signature(getattr(ExchangeAdapter, name)).parameters.keys()
    )


async def test_get_ticker():
    t = await _adapter(
        {
            "lastPrice": "100.5",
            "bidPrice": "100",
            "askPrice": "101",
            "volume": "7",
            "closeTime": 1700000000000,
        }
    ).get_ticker("BTC/USDT")
    assert t.price == Decimal("100.5") and t.exchange == "binance"


async def test_get_orderbook():
    ob = await _adapter({"bids": [["1", "2"]], "asks": [["3", "4"]]}).get_orderbook("BTC/USDT", 5)
    assert ob.bids[0].price == Decimal("1") and ob.asks[0].quantity == Decimal("4")


async def test_get_ohlcv():
    row = [1700000000000, "1", "2", "0.5", "1.5", "10", 1700000059999]
    c = await _adapter([row]).get_ohlcv("BTC/USDT", "1m", 1)
    assert c[0].high == Decimal("2") and c[0].close_time > c[0].open_time


async def test_malformed_ticker_is_fatal():
    with pytest.raises(FatalExchangeError):
        await _adapter({"lastPrice": "x"}).get_ticker("BTC/USDT")


async def test_malformed_klines_is_fatal():
    with pytest.raises(FatalExchangeError):
        await _adapter([[1]]).get_ohlcv("BTC/USDT", "1m")


def test_venue_profile_wired():
    a = BinanceAdapter("k", "s")
    assert a.venue_profile() is BINANCE_SPOT_PROFILE
    assert BINANCE_SPOT_PROFILE.verified == "DOC_ONLY"


def test_registered_via_br9_extension_point():
    register_exchange_adapter_factory(bfactory.EXCHANGE_NAME, bfactory._binance_adapter_factory)
    assert isinstance(build_adapter("binance", "k", "s", {}, demo_mode=True), BinanceAdapter)


async def test_balance_unsupported_fail_closed():
    with pytest.raises(Exception, match="get_balance"):
        await BinanceAdapter("k", "s").get_balance()
