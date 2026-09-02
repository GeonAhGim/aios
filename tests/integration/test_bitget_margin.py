"""02b_bitget_api_v2_full_spec_v1.md §4 통합테스트 — Margin P0.

실제 Bitget Demo 계정 API 키가 없는 상태라 httpx.MockTransport로 응답
형태를 재현해 검증한다(test_bitget_adapter.py와 동일 원칙) — 필드명은
커뮤니티 SDK 레퍼런스 기준 최선 추정치라 라이브 검증 전까지는 확정 아님.
"""
import json
from decimal import Decimal

import httpx
import pytest

from src.data.models.base import AssetClass
from src.data.models.trading import Order, OrderSide, OrderStatus, OrderType
from src.exchanges.bitget.adapter import BitgetAdapter
from src.exchanges.bitget.margin_mixin import CROSSED, ISOLATED


def _make_adapter(handler) -> BitgetAdapter:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(base_url="https://api.bitget.com", transport=transport)
    return BitgetAdapter("key", "secret", "passphrase", demo_mode=True, http_client=client)


def _json_response(payload: dict, status_code: int = 200) -> httpx.Response:
    return httpx.Response(status_code, json=payload)


async def test_get_margin_account_assets_crossed():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/margin/crossed/account/assets"
        return _json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [
                    {
                        "coin": "usdt",
                        "available": "100",
                        "borrow": "20",
                        "interest": "0.5",
                        "netAsset": "80",
                        "riskRate": "0.3",
                    }
                ],
            }
        )

    adapter = _make_adapter(handler)
    assets = await adapter.get_margin_account_assets(CROSSED)

    assert assets[0].margin_type == "crossed"
    assert assets[0].coin == "USDT"
    assert assets[0].borrowed == Decimal("20")
    assert assets[0].risk_rate == Decimal("0.3")


async def test_get_margin_account_assets_isolated_with_symbol():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/margin/isolated/account/assets"
        assert request.url.params["symbol"] == "BTCUSDT"
        return _json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [
                    {"symbol": "BTCUSDT", "coin": "usdt", "available": "50", "borrow": "0"}
                ],
            }
        )

    adapter = _make_adapter(handler)
    assets = await adapter.get_margin_account_assets(ISOLATED, symbol="BTC/USDT")

    assert assets[0].symbol == "BTCUSDT"
    assert assets[0].net_asset == Decimal("50")  # netAsset 없으면 available로 폴백
    assert assets[0].risk_rate is None


async def test_invalid_margin_type_raises_value_error():
    adapter = _make_adapter(lambda request: _json_response({"code": "00000", "data": []}))
    with pytest.raises(ValueError):
        await adapter.get_margin_account_assets("bogus")


async def test_get_margin_risk_rate():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/margin/crossed/account/risk-rate"
        return _json_response(
            {"code": "00000", "msg": "success", "requestTime": 1, "data": {"riskRate": "0.42"}}
        )

    adapter = _make_adapter(handler)
    risk_rate = await adapter.get_margin_risk_rate(CROSSED)

    assert risk_rate == Decimal("0.42")


async def test_place_margin_order():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/margin/crossed/place-order"
        body = json.loads(request.content)
        assert body["baseSize"] == "0.01"
        return _json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": {"orderId": "555", "clientOid": "c-1"},
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

    result = await adapter.place_margin_order(CROSSED, order)

    assert result.exchange_order_id == "555"
    assert result.status == OrderStatus.SUBMITTED


async def test_cancel_margin_order():
    adapter = _make_adapter(
        lambda request: _json_response(
            {"code": "00000", "msg": "success", "requestTime": 1, "data": {"orderId": "555"}}
        )
    )
    assert await adapter.cancel_margin_order(CROSSED, "555") is True


async def test_get_margin_open_orders():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/margin/isolated/open-orders"
        return _json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [
                    {
                        "orderId": "555",
                        "clientOid": "c-1",
                        "symbol": "BTCUSDT",
                        "side": "buy",
                        "orderType": "limit",
                        "baseSize": "0.01",
                        "status": "live",
                    }
                ],
            }
        )

    adapter = _make_adapter(handler)
    orders = await adapter.get_margin_open_orders(ISOLATED)

    assert orders[0].exchange_order_id == "555"
    assert orders[0].quantity == Decimal("0.01")
