"""02c_bitget_api_v2_extended_spec_v1.md §1.4 통합테스트 — Earn(적금/이자상품).

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


async def test_get_earn_products_filters_by_coin():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/earn/savings/product"
        assert request.url.params["coin"] == "USDT"
        return _json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [{"productId": "p-1", "coin": "USDT", "apy": "0.05"}],
            }
        )

    adapter = _make_adapter(handler)
    products = await adapter.get_earn_products(coin="usdt")

    assert products == [{"productId": "p-1", "coin": "USDT", "apy": "0.05"}]


async def test_subscribe_earn_product_sends_amount():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/earn/savings/subscribe"
        body = json.loads(request.content)
        assert body == {"productId": "p-1", "amount": "100", "periodType": "flexible"}
        return _json_response(
            {"code": "00000", "msg": "success", "requestTime": 1, "data": {"orderId": "o-1"}}
        )

    adapter = _make_adapter(handler)
    result = await adapter.subscribe_earn_product("p-1", Decimal("100"))

    assert result == {"orderId": "o-1"}


async def test_redeem_earn_product_sends_amount():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/earn/savings/redeem"
        body = json.loads(request.content)
        assert body == {"orderId": "o-1", "amount": "50"}
        return _json_response(
            {"code": "00000", "msg": "success", "requestTime": 1, "data": {"orderId": "o-1"}}
        )

    adapter = _make_adapter(handler)
    result = await adapter.redeem_earn_product("o-1", Decimal("50"))

    assert result == {"orderId": "o-1"}


async def test_get_earn_assets_returns_raw_rows():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/earn/savings/assets"
        return _json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [{"coin": "USDT", "amount": "100"}],
            }
        )

    adapter = _make_adapter(handler)
    assets = await adapter.get_earn_assets()

    assert assets == [{"coin": "USDT", "amount": "100"}]


async def test_get_earn_records_returns_raw_rows():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/earn/savings/records"
        return _json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [{"orderId": "o-1", "type": "subscribe"}],
            }
        )

    adapter = _make_adapter(handler)
    records = await adapter.get_earn_records()

    assert records == [{"orderId": "o-1", "type": "subscribe"}]
