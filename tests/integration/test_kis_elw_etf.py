"""02d_kis_api_full_spec_v1.md §4 통합테스트 — ELW/ETF/ETN.

httpx.MockTransport 기반 검증(test_kis_adapter.py와 동일 원칙).
"""
from decimal import Decimal

import httpx

from src.exchanges.kis.adapter import KISAdapter

TOKEN_RESPONSE = {"access_token": "tok-1", "access_token_token_expired": "2099-01-01 00:00:00"}


def _make_adapter(handler) -> KISAdapter:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(
        base_url="https://openapivts.koreainvestment.com:29443", transport=transport
    )
    return KISAdapter("app", "secret", "12345678", "01", is_paper_trading=True, http_client=client)


def _route(request: httpx.Request, routes: dict) -> httpx.Response:
    if request.url.path == "/oauth2/tokenP":
        return httpx.Response(200, json=TOKEN_RESPONSE)
    handler = routes.get(request.url.path)
    assert handler is not None, f"no route for {request.url.path}"
    return handler(request)


async def test_get_elw_price_parses_output():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["tr_id"] == "FHKEW15010000"
        assert request.url.params["FID_COND_MRKT_DIV_CODE"] == "W"
        return httpx.Response(
            200,
            json={"rt_cd": "0", "msg1": "ok", "output": {"stck_prpr": "150", "acml_vol": "500"}},
        )

    adapter = _make_adapter(
        lambda request: _route(
            request, {"/uapi/domestic-stock/v1/quotations/inquire-elw-price": handler}
        )
    )
    ticker = await adapter.get_elw_price("58J300")

    assert ticker.price == Decimal("150")


async def test_get_etf_price_parses_output():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["tr_id"] == "FHPST02400000"
        assert request.url.params["FID_COND_MRKT_DIV_CODE"] == "J"
        return httpx.Response(
            200,
            json={"rt_cd": "0", "msg1": "ok", "output": {"stck_prpr": "10000", "acml_vol": "2000"}},
        )

    adapter = _make_adapter(
        lambda request: _route(request, {"/uapi/etfetn/v1/quotations/inquire-price": handler})
    )
    ticker = await adapter.get_etf_price("069500")

    assert ticker.price == Decimal("10000")
