"""02b_bitget_api_v2_full_spec_v1.md §5 통합테스트 — Futures/Mix P0 (Account/Position).

실제 Bitget Demo 계정 API 키가 없는 상태라 httpx.MockTransport로 응답
형태를 재현해 검증한다(test_bitget_adapter.py와 동일 원칙) — 필드명은
커뮤니티 SDK 레퍼런스 기준 최선 추정치라 라이브 검증 전까지는 확정 아님.
"""
import json
from decimal import Decimal

import httpx

from tests.integration.bitget_futures_doubles import json_response, make_adapter


async def test_get_futures_accounts():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/mix/account/accounts"
        return json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [
                    {
                        "marginCoin": "usdt",
                        "available": "1000",
                        "accountEquity": "1200",
                        "locked": "0",
                    }
                ],
            }
        )

    adapter = make_adapter(handler)
    balances = await adapter.get_futures_accounts()

    assert balances[0].asset == "USDT"
    assert balances[0].total == Decimal("1200")


async def test_set_futures_leverage():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/mix/account/set-leverage"
        body = json.loads(request.content)
        assert body["leverage"] == "5"
        return json_response({"code": "00000", "msg": "success", "requestTime": 1, "data": {}})

    adapter = make_adapter(handler)
    await adapter.set_futures_leverage("BTC/USDT", Decimal("5"))


async def test_set_futures_margin_mode_rejects_invalid_value():
    adapter = make_adapter(lambda request: json_response({"code": "00000", "data": {}}))
    try:
        await adapter.set_futures_margin_mode("BTC/USDT", "bogus")
        raise AssertionError("ValueError를 던졌어야 함")
    except ValueError:
        pass


async def test_get_futures_liquidation_price():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/mix/account/liq-price"
        return json_response(
            {"code": "00000", "msg": "success", "requestTime": 1, "data": {"liqPx": "70000"}}
        )

    adapter = make_adapter(handler)
    price = await adapter.get_futures_liquidation_price("BTC/USDT")

    assert price == Decimal("70000")


async def test_get_futures_position_returns_none_when_empty():
    adapter = make_adapter(
        lambda request: json_response(
            {"code": "00000", "msg": "success", "requestTime": 1, "data": []}
        )
    )
    assert await adapter.get_futures_position("BTC/USDT") is None


async def test_get_futures_position_parses_row():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/mix/position/single-position"
        return json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [
                    {
                        "total": "0.5",
                        "openPriceAvg": "80000",
                        "markPrice": "81000",
                        "unrealizedPL": "500",
                        "achievedProfits": "0",
                        "leverage": "10",
                        "marginSize": "4000",
                    }
                ],
            }
        )

    adapter = make_adapter(handler)
    position = await adapter.get_futures_position("BTC/USDT")

    assert position is not None
    assert position.quantity == Decimal("0.5")
    assert position.leverage == Decimal("10")


async def test_get_futures_positions():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/mix/position/all-position"
        return json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [{"symbol": "BTCUSDT", "total": "0.5", "openPriceAvg": "80000"}],
            }
        )

    adapter = make_adapter(handler)
    positions = await adapter.get_futures_positions()

    assert positions[0].symbol == "BTCUSDT"


async def test_get_futures_account_single():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/mix/account/account"
        return json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": {"marginCoin": "USDT", "available": "1000", "locked": "0"},
            }
        )

    adapter = make_adapter(handler)
    balance = await adapter.get_futures_account("BTC/USDT")

    assert balance.asset == "USDT"
    assert balance.available == Decimal("1000")


async def test_set_futures_margin_sends_amount():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/mix/account/set-margin"
        body = json.loads(request.content)
        assert body["amount"] == "50"
        return json_response({"code": "00000", "msg": "success", "requestTime": 1, "data": {}})

    adapter = make_adapter(handler)
    await adapter.set_futures_margin("BTC/USDT", Decimal("50"))


async def test_get_futures_max_open_amount():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/mix/account/max-open"
        return json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": {"maxOpenAvailable": "10"},
            }
        )

    adapter = make_adapter(handler)
    amount = await adapter.get_futures_max_open_amount("BTC/USDT")

    assert amount == Decimal("10")


async def test_get_futures_account_bills_returns_raw_rows():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/mix/account/bill"
        return json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [{"billId": "b-1", "amount": "10"}],
            }
        )

    adapter = make_adapter(handler)
    bills = await adapter.get_futures_account_bills()

    assert bills == [{"billId": "b-1", "amount": "10"}]


async def test_get_futures_position_history_returns_raw_rows():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/mix/position/history-position"
        return json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [{"symbol": "BTCUSDT", "netProfit": "12.5"}],
            }
        )

    adapter = make_adapter(handler)
    history = await adapter.get_futures_position_history(symbol="BTC/USDT")

    assert history == [{"symbol": "BTCUSDT", "netProfit": "12.5"}]
