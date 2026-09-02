"""02c_bitget_api_v2_extended_spec_v1.md §1.8 통합테스트 — Copy Trading.

httpx.MockTransport 기반 검증(test_bitget_adapter.py와 동일 원칙).
"""
import json
from decimal import Decimal

import httpx

from src.exchanges.bitget.adapter import BitgetAdapter


def _make_adapter(handler) -> BitgetAdapter:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(base_url="https://api.bitget.com", transport=transport)
    return BitgetAdapter("key", "secret", "passphrase", demo_mode=True, http_client=client)


def _json_response(payload: dict, status_code: int = 200) -> httpx.Response:
    return httpx.Response(status_code, json=payload)


async def test_get_copy_trading_traders_returns_raw_rows():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/copy/mix-follower/query-traders"
        return _json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [{"traderId": "t-1"}],
            }
        )

    adapter = _make_adapter(handler)
    traders = await adapter.get_copy_trading_traders()

    assert traders == [{"traderId": "t-1"}]


async def test_follow_copy_trader_sends_amount():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/copy/mix-follower/setting"
        body = json.loads(request.content)
        assert body["traderId"] == "t-1"
        assert body["copyAmount"] == "100"
        return _json_response(
            {"code": "00000", "msg": "success", "requestTime": 1, "data": {"traderId": "t-1"}}
        )

    adapter = _make_adapter(handler)
    result = await adapter.follow_copy_trader("t-1", copy_amount=Decimal("100"))

    assert result == {"traderId": "t-1"}


async def test_unfollow_copy_trader_returns_true_on_success():
    adapter = _make_adapter(
        lambda request: _json_response(
            {"code": "00000", "msg": "success", "requestTime": 1, "data": {}}
        )
    )
    assert await adapter.unfollow_copy_trader("t-1") is True


async def test_get_copy_trading_current_orders_returns_raw_rows():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/copy/mix-follower/query-current-orders"
        return _json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [{"orderId": "co-1"}],
            }
        )

    adapter = _make_adapter(handler)
    orders = await adapter.get_copy_trading_current_orders()

    assert orders == [{"orderId": "co-1"}]


async def test_get_copy_trading_order_history_returns_raw_rows():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/copy/mix-follower/query-history-orders"
        return _json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [{"orderId": "co-1", "status": "closed"}],
            }
        )

    adapter = _make_adapter(handler)
    history = await adapter.get_copy_trading_order_history()

    assert history == [{"orderId": "co-1", "status": "closed"}]


async def test_get_copy_trading_followers_returns_raw_rows():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/copy/mix-trader/config-query-followers"
        return _json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [{"followerId": "f-1"}],
            }
        )

    adapter = _make_adapter(handler)
    followers = await adapter.get_copy_trading_followers()

    assert followers == [{"followerId": "f-1"}]


async def test_update_copy_trading_trader_profile_sends_kwargs():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/copy/mix-trader/config-settings-base"
        body = json.loads(request.content)
        assert body == {"profitShareRatio": "0.1"}
        return _json_response(
            {"code": "00000", "msg": "success", "requestTime": 1, "data": {"ok": True}}
        )

    adapter = _make_adapter(handler)
    result = await adapter.update_copy_trading_trader_profile(profitShareRatio="0.1")

    assert result == {"ok": True}


async def test_get_copy_trading_profit_summary_returns_dict():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/copy/mix-trader/order-profit-history-summary"
        return _json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": {"totalProfit": "500"},
            }
        )

    adapter = _make_adapter(handler)
    summary = await adapter.get_copy_trading_profit_summary()

    assert summary == {"totalProfit": "500"}
