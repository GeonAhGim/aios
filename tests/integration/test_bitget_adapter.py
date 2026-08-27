"""6.11 — BitgetAdapter 통합 테스트.

실제 Bitget Demo 계정 API 키가 없는 상태(.env BITGET_API_KEY 비어있음)라
httpx.MockTransport로 실제 캡처한 Bitget 응답 형태를 재현해 검증한다.
실제 Demo 계좌 왕복 테스트(주문 생성→조회→취소)는 사용자가 API 키를
채운 뒤 별도로 수행해야 한다(08번 §8.3 원본 의도).
"""
import json
from decimal import Decimal

import httpx
import pytest

from src.core.exceptions import FatalExchangeError, RetryableExchangeError
from src.data.models.base import AssetClass
from src.data.models.trading import Order, OrderSide, OrderStatus, OrderType
from src.exchanges.bitget.adapter import BitgetAdapter

REAL_TICKER_ENVELOPE = {
    "code": "00000",
    "msg": "success",
    "requestTime": 1787851010117,
    "data": [
        {
            "open": "78217.08",
            "symbol": "BTCUSDT",
            "high24h": "80800",
            "low24h": "78196",
            "lastPr": "80663.08",
            "quoteVolume": "270635812.383435",
            "baseVolume": "3407.420693",
            "usdtVolume": "270635812.38343424",
            "ts": "1787851009318",
            "bidPr": "80664.02",
            "askPr": "80664.03",
            "bidSz": "0.859943",
            "askSz": "0.29158",
            "openUtc": "79023.47",
            "changeUtc24h": "0.02075",
            "change24h": "0.03127",
        }
    ],
}


def _make_adapter(handler) -> BitgetAdapter:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(base_url="https://api.bitget.com", transport=transport)
    return BitgetAdapter("key", "secret", "passphrase", demo_mode=True, http_client=client)


def _json_response(payload: dict, status_code: int = 200) -> httpx.Response:
    return httpx.Response(status_code, json=payload)


async def test_get_ticker_parses_real_response_shape():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/spot/market/tickers"
        assert request.headers["ACCESS-KEY"] == "key"
        assert request.headers["paptrading"] == "1"
        return _json_response(REAL_TICKER_ENVELOPE)

    adapter = _make_adapter(handler)
    ticker = await adapter.get_ticker("BTC/USDT")

    assert ticker.symbol == "BTC/USDT"
    assert ticker.price == Decimal("80663.08")


async def test_capabilities_declare_crypto_only():
    adapter = _make_adapter(lambda request: _json_response(REAL_TICKER_ENVELOPE))
    caps = adapter.get_capabilities()

    assert caps.supported_asset_classes == [AssetClass.CRYPTO]
    assert caps.supports_futures is False


async def test_api_error_response_raises_retryable_by_default():
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response({"code": "99999", "msg": "internal error", "data": {}})

    adapter = _make_adapter(handler)
    with pytest.raises(RetryableExchangeError):
        await adapter.get_ticker("BTC/USDT")


async def test_signature_error_code_raises_fatal():
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response({"code": "40012", "msg": "invalid sign", "data": {}})

    adapter = _make_adapter(handler)
    with pytest.raises(FatalExchangeError):
        await adapter.get_ticker("BTC/USDT")


async def test_get_balance_maps_coin_amounts():
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [
                    {"coin": "usdt", "available": "100", "frozen": "5", "locked": "0"},
                ],
            }
        )

    adapter = _make_adapter(handler)
    balances = await adapter.get_balance()

    assert balances[0].asset == "USDT"
    assert balances[0].total == Decimal("105")
    assert balances[0].available == Decimal("100")


async def test_get_positions_always_empty_for_spot():
    adapter = _make_adapter(lambda request: _json_response(REAL_TICKER_ENVELOPE))
    assert await adapter.get_positions() == []


async def test_place_order_returns_order_with_exchange_id():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        body = json.loads(request.content)
        assert body["symbol"] == "BTCUSDT"
        assert body["side"] == "buy"
        return _json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": {"orderId": "999", "clientOid": "c-1"},
            }
        )

    adapter = _make_adapter(handler)
    order = Order(
        client_order_id="c-1",
        strategy_id="s-1",
        strategy_version="v1",
        symbol="BTC/USDT",
        exchange="bitget",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("0.01"),
        asset_class=AssetClass.CRYPTO,
    )

    result = await adapter.place_order(order)

    assert result.exchange_order_id == "999"
    assert result.status == OrderStatus.SUBMITTED


async def test_cancel_order_returns_true_on_success():
    adapter = _make_adapter(
        lambda request: _json_response(
            {"code": "00000", "msg": "success", "requestTime": 1, "data": {"orderId": "999"}}
        )
    )
    assert await adapter.cancel_order("999") is True


async def test_health_check_returns_false_on_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response({"code": "99999", "msg": "error", "data": {}})

    adapter = _make_adapter(handler)
    assert await adapter.health_check() is False


async def test_health_check_returns_true_on_success():
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response({"code": "00000", "msg": "success", "requestTime": 1, "data": []})

    adapter = _make_adapter(handler)
    assert await adapter.health_check() is True
