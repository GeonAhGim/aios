"""02c_bitget_api_v2_extended_spec_v1.md §1.3 통합테스트 — P2P.

httpx.MockTransport 기반 검증(test_bitget_adapter.py와 동일 원칙).
"""
import httpx

from src.exchanges.bitget.adapter import BitgetAdapter


def _make_adapter(handler) -> BitgetAdapter:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(base_url="https://api.bitget.com", transport=transport)
    return BitgetAdapter("key", "secret", "passphrase", demo_mode=True, http_client=client)


def _json_response(payload: dict, status_code: int = 200) -> httpx.Response:
    return httpx.Response(status_code, json=payload)


async def test_get_p2p_ads_filters_by_coin():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/p2p/advList"
        assert request.url.params["coin"] == "USDT"
        return _json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [{"advId": "a-1", "coin": "USDT"}],
            }
        )

    adapter = _make_adapter(handler)
    ads = await adapter.get_p2p_ads(coin="usdt")

    assert ads == [{"advId": "a-1", "coin": "USDT"}]


async def test_get_p2p_merchant_info_returns_dict():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/p2p/merchantInfo"
        return _json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": {"merchantId": "m-1", "nickname": "trader1"},
            }
        )

    adapter = _make_adapter(handler)
    info = await adapter.get_p2p_merchant_info()

    assert info == {"merchantId": "m-1", "nickname": "trader1"}


async def test_get_p2p_orders_filters_by_status():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/p2p/orderList"
        assert request.url.params["status"] == "completed"
        return _json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [{"orderId": "o-1", "status": "completed"}],
            }
        )

    adapter = _make_adapter(handler)
    orders = await adapter.get_p2p_orders(status="completed")

    assert orders == [{"orderId": "o-1", "status": "completed"}]


async def test_get_p2p_merchants_returns_raw_rows():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/p2p/merchantList"
        return _json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [{"merchantId": "m-1"}],
            }
        )

    adapter = _make_adapter(handler)
    merchants = await adapter.get_p2p_merchants()

    assert merchants == [{"merchantId": "m-1"}]
