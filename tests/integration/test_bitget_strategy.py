"""02c_bitget_api_v2_extended_spec_v1.md §1.10 통합테스트 — Strategy(전략주문).

httpx.MockTransport 기반 검증(test_bitget_adapter.py와 동일 원칙).
"""
import json
from decimal import Decimal

import httpx
import pytest

from src.core.exceptions import FrozenZonePaperAdapterBlockedError
from src.exchanges.bitget.adapter import BitgetAdapter


def _make_adapter(handler) -> BitgetAdapter:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(base_url="https://api.bitget.com", transport=transport)
    return BitgetAdapter("key", "secret", "passphrase", demo_mode=True, http_client=client)


def _json_response(payload: dict, status_code: int = 200) -> httpx.Response:
    return httpx.Response(status_code, json=payload)


async def test_place_strategy_order_sends_type():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/trace/strategy/place-order"
        body = json.loads(request.content)
        assert body["strategyType"] == "twap"
        assert body["totalAmount"] == "1"
        return _json_response(
            {"code": "00000", "msg": "success", "requestTime": 1, "data": {"orderId": "s-1"}}
        )

    adapter = _make_adapter(handler)
    result = await adapter.place_strategy_order(
        "BTC/USDT", "buy", "twap", Decimal("1"), duration_seconds=3600
    )

    assert result == {"orderId": "s-1"}


async def test_place_strategy_order_blocked_on_live_configured_adapter():
    """레드팀 #2026-09-02-32 회귀 테스트."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("가드가 막았어야 할 요청이 실제로 나갔습니다.")

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(base_url="https://api.bitget.com", transport=transport)
    live_adapter = BitgetAdapter(
        "key", "secret", "passphrase", demo_mode=False, http_client=client
    )

    with pytest.raises(FrozenZonePaperAdapterBlockedError):
        await live_adapter.place_strategy_order("BTC/USDT", "buy", "twap", Decimal("1"))


async def test_cancel_strategy_order_returns_true_on_success():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/trace/strategy/cancel-order"
        return _json_response({"code": "00000", "msg": "success", "requestTime": 1, "data": {}})

    adapter = _make_adapter(handler)
    assert await adapter.cancel_strategy_order("s-1", symbol="BTC/USDT") is True


async def test_get_current_strategy_orders_returns_raw_rows():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/trace/strategy/current-order"
        return _json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [{"orderId": "s-1", "status": "running"}],
            }
        )

    adapter = _make_adapter(handler)
    orders = await adapter.get_current_strategy_orders()

    assert orders == [{"orderId": "s-1", "status": "running"}]


async def test_get_strategy_order_history_returns_raw_rows():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/trace/strategy/history-order"
        return _json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [{"orderId": "s-1", "status": "completed"}],
            }
        )

    adapter = _make_adapter(handler)
    history = await adapter.get_strategy_order_history()

    assert history == [{"orderId": "s-1", "status": "completed"}]
