"""02c_bitget_api_v2_extended_spec_v1.md §1.9 통합테스트 — Grid(그리드봇).

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


async def test_place_spot_grid_sends_range():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/spot/grid/place-grid"
        body = json.loads(request.content)
        assert body["lowerLimit"] == "70000"
        assert body["upperLimit"] == "90000"
        assert body["gridNum"] == "10"
        return _json_response(
            {"code": "00000", "msg": "success", "requestTime": 1, "data": {"gridId": "g-1"}}
        )

    adapter = _make_adapter(handler)
    result = await adapter.place_spot_grid(
        "BTC/USDT", Decimal("70000"), Decimal("90000"), 10, Decimal("1000")
    )

    assert result == {"gridId": "g-1"}


async def test_place_futures_grid_sends_product_type():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/mix/grid/place-grid"
        body = json.loads(request.content)
        assert body["productType"] == "USDT-FUTURES"
        return _json_response(
            {"code": "00000", "msg": "success", "requestTime": 1, "data": {"gridId": "g-2"}}
        )

    adapter = _make_adapter(handler)
    result = await adapter.place_futures_grid(
        "BTC/USDT", Decimal("70000"), Decimal("90000"), 10, Decimal("1000")
    )

    assert result == {"gridId": "g-2"}


async def test_close_grid_returns_true_on_success():
    adapter = _make_adapter(
        lambda request: _json_response(
            {"code": "00000", "msg": "success", "requestTime": 1, "data": {}}
        )
    )
    assert await adapter.close_grid("g-1") is True


async def test_get_current_grids_returns_raw_rows():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/spot/grid/current-grid"
        return _json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [{"gridId": "g-1", "status": "running"}],
            }
        )

    adapter = _make_adapter(handler)
    grids = await adapter.get_current_grids()

    assert grids == [{"gridId": "g-1", "status": "running"}]


async def test_get_grid_history_returns_raw_rows():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/spot/grid/grid-history"
        return _json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [{"gridId": "g-1", "status": "closed"}],
            }
        )

    adapter = _make_adapter(handler)
    history = await adapter.get_grid_history()

    assert history == [{"gridId": "g-1", "status": "closed"}]


async def test_get_grid_profit_returns_dict():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/spot/grid/grid-profit"
        assert request.url.params["gridId"] == "g-1"
        return _json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": {"gridId": "g-1", "totalProfit": "12.5"},
            }
        )

    adapter = _make_adapter(handler)
    profit = await adapter.get_grid_profit("g-1")

    assert profit == {"gridId": "g-1", "totalProfit": "12.5"}
