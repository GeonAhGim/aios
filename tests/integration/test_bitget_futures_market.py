"""02b_bitget_api_v2_full_spec_v1.md §5 통합테스트 — Futures/Mix P0 (Market).

실제 Bitget Demo 계정 API 키가 없는 상태라 httpx.MockTransport로 응답
형태를 재현해 검증한다(test_bitget_adapter.py와 동일 원칙) — 필드명은
커뮤니티 SDK 레퍼런스 기준 최선 추정치라 라이브 검증 전까지는 확정 아님.
"""
from decimal import Decimal

import httpx

from tests.integration.bitget_futures_doubles import json_response, make_adapter


async def test_get_futures_contracts():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/mix/market/contracts"
        assert request.url.params["productType"] == "USDT-FUTURES"
        return json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [
                    {
                        "symbol": "BTCUSDT",
                        "baseCoin": "BTC",
                        "quoteCoin": "USDT",
                        "minTradeNum": "0.001",
                        "priceEndStep": "0.1",
                        "volumePlace": "3",
                        "maxLever": "125",
                    }
                ],
            }
        )

    adapter = make_adapter(handler)
    contracts = await adapter.get_futures_contracts()

    assert contracts[0].symbol == "BTCUSDT"
    assert contracts[0].max_leverage == Decimal("125")


async def test_get_futures_ticker():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/mix/market/ticker"
        return json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [{"lastPr": "81000", "bidPr": "80999", "askPr": "81001"}],
            }
        )

    adapter = make_adapter(handler)
    ticker = await adapter.get_futures_ticker("BTC/USDT")

    assert ticker.price == Decimal("81000")


async def test_get_futures_orderbook():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/mix/market/merge-depth"
        return json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": {"bids": [["80900", "1.5"]], "asks": [["80950", "2.0"]]},
            }
        )

    adapter = make_adapter(handler)
    book = await adapter.get_futures_orderbook("BTC/USDT")

    assert book.bids[0].price == Decimal("80900")
    assert book.asks[0].quantity == Decimal("2.0")


async def test_get_futures_candles():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/mix/market/candles"
        assert request.url.params["granularity"] == "1H"
        return json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [["1700000000000", "80000", "81000", "79500", "80500", "12.3"]],
            }
        )

    adapter = make_adapter(handler)
    candles = await adapter.get_futures_candles("BTC/USDT", "1h")

    assert candles[0].close == Decimal("80500")


async def test_get_futures_current_funding_rate():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/mix/market/current-fund-rate"
        return json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [{"fundingRate": "0.0001", "nextUpdate": "1700003600000"}],
            }
        )

    adapter = make_adapter(handler)
    rate = await adapter.get_futures_current_funding_rate("BTC/USDT")

    assert rate.current_rate == Decimal("0.0001")


async def test_get_futures_tickers_converts_symbol_to_canonical():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/mix/market/tickers"
        return json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [{"symbol": "BTCUSDT", "lastPr": "80000", "baseVolume": "100"}],
            }
        )

    adapter = make_adapter(handler)
    tickers = await adapter.get_futures_tickers()

    assert tickers[0].symbol == "BTC/USDT"
    assert tickers[0].price == Decimal("80000")


async def test_get_futures_history_candles():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/mix/market/history-candles"
        assert request.url.params["endTime"] == "1700000000000"
        return json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [["1700000000000", "80000", "81000", "79500", "80500", "12.3"]],
            }
        )

    adapter = make_adapter(handler)
    candles = await adapter.get_futures_history_candles(
        "BTC/USDT", "1h", end_time="1700000000000"
    )

    assert candles[0].close == Decimal("80500")


async def test_get_futures_history_funding_rate():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/mix/market/history-fund-rate"
        return json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [{"fundingRate": "0.0001", "fundingTime": "1700000000000"}],
            }
        )

    adapter = make_adapter(handler)
    rates = await adapter.get_futures_history_funding_rate("BTC/USDT")

    assert rates[0].current_rate == Decimal("0.0001")


async def test_get_futures_funding_time():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/mix/market/funding-time"
        return json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": {"nextFundingTime": "1700000000000"},
            }
        )

    adapter = make_adapter(handler)
    next_time = await adapter.get_futures_funding_time("BTC/USDT")

    assert next_time.year == 2023


async def test_get_futures_open_interest():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/mix/market/open-interest"
        return json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": {"openInterestList": [{"symbol": "BTCUSDT", "size": "1234.5"}]},
            }
        )

    adapter = make_adapter(handler)
    oi = await adapter.get_futures_open_interest("BTC/USDT")

    assert oi == Decimal("1234.5")


async def test_get_futures_position_lever_tiers():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/mix/market/query-position-lever"
        return json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [{"level": "1", "maxLever": "125"}],
            }
        )

    adapter = make_adapter(handler)
    tiers = await adapter.get_futures_position_lever_tiers("BTC/USDT")

    assert tiers == [{"level": "1", "maxLever": "125"}]
