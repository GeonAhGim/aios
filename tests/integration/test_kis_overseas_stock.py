"""02d_kis_api_full_spec_v1.md §4 통합테스트 — 해외주식(overseas_stock).

httpx.MockTransport 기반 검증(test_kis_adapter.py와 동일 원칙). tr_id는
전부 실전 기준 "T"로 시작해 모의투자(is_paper_trading=True) 헤더에선
T→V로 치환된다(기존 KISAdapter._resolve_tr_id() 규칙 재확인).
"""
import json
from decimal import Decimal

import httpx
import pytest

from src.data.models.base import AssetClass
from src.data.models.trading import Order, OrderSide, OrderStatus, OrderType
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


def _order(**overrides) -> Order:
    defaults = dict(
        client_order_id="c-1",
        strategy_id="s-1",
        strategy_version="v1",
        symbol="AAPL",
        exchange="kis",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=Decimal("1"),
        asset_class=AssetClass.US_EQUITY,
    )
    defaults.update(overrides)
    return Order(**defaults)


async def test_get_overseas_ticker_uses_quote_exchange_code():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["tr_id"] == "HHDFS00000300"
        assert request.url.params["EXCD"] == "NAS"
        assert request.url.params["SYMB"] == "AAPL"
        return httpx.Response(
            200, json={"rt_cd": "0", "msg1": "ok", "output": {"last": "225.50", "tvol": "1000"}}
        )

    adapter = _make_adapter(
        lambda request: _route(request, {"/uapi/overseas-price/v1/quotations/price": handler})
    )
    ticker = await adapter.get_overseas_ticker("AAPL", "US")

    assert ticker.price == Decimal("225.50")


async def test_get_overseas_ticker_rejects_unknown_market():
    adapter = _make_adapter(lambda request: httpx.Response(200, json=TOKEN_RESPONSE))
    with pytest.raises(ValueError):
        await adapter.get_overseas_ticker("AAPL", "MARS")


async def test_place_overseas_order_uses_order_exchange_code_and_buy_tr_id():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["tr_id"] == "VTTT1002U"  # 모의투자 치환 확인(TTTT1002U -> V)
        body = json.loads(request.content)
        assert body["OVRS_EXCG_CD"] == "NASD"
        assert body["PDNO"] == "AAPL"
        return httpx.Response(
            200,
            json={
                "rt_cd": "0",
                "msg1": "ok",
                "output": {"KRX_FWDG_ORD_ORGNO": "1234", "ODNO": "999"},
            },
        )

    adapter = _make_adapter(
        lambda request: _route(request, {"/uapi/overseas-stock/v1/trading/order": handler})
    )
    order = _order()

    result = await adapter.place_overseas_order(order, "US")

    assert result.exchange_order_id == "1234:999"
    assert result.status == OrderStatus.SUBMITTED


async def test_place_overseas_order_sell_uses_sell_tr_id():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["tr_id"] == "VTTT1006U"  # sell tr_id(TTTT1006U)의 모의투자 치환
        return httpx.Response(
            200,
            json={
                "rt_cd": "0",
                "msg1": "ok",
                "output": {"KRX_FWDG_ORD_ORGNO": "1234", "ODNO": "999"},
            },
        )

    adapter = _make_adapter(
        lambda request: _route(request, {"/uapi/overseas-stock/v1/trading/order": handler})
    )
    order = _order(side=OrderSide.SELL)

    await adapter.place_overseas_order(order, "US")


async def test_cancel_overseas_order_returns_true_on_success():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["tr_id"] == "VTTT1004U"  # 모의투자 치환 확인
        body = json.loads(request.content)
        assert body["ORGN_ODNO"] == "999"
        return httpx.Response(200, json={"rt_cd": "0", "msg1": "ok", "output": {}})

    adapter = _make_adapter(
        lambda request: _route(
            request, {"/uapi/overseas-stock/v1/trading/order-rvsecncl": handler}
        )
    )
    result = await adapter.cancel_overseas_order(
        "1234:999", "AAPL", "US", original_quantity=Decimal("1")
    )

    assert result is True


async def test_get_overseas_balance_maps_holdings():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["tr_id"] == "VTTS3012R"  # 모의투자 치환 확인
        assert request.url.params["OVRS_EXCG_CD"] == "NASD"
        return httpx.Response(
            200,
            json={
                "rt_cd": "0",
                "msg1": "ok",
                "output1": [
                    {"ovrs_pdno": "AAPL", "ovrs_cblc_qty": "10", "ord_psbl_qty": "10"}
                ],
            },
        )

    adapter = _make_adapter(
        lambda request: _route(
            request, {"/uapi/overseas-stock/v1/trading/inquire-balance": handler}
        )
    )
    balances = await adapter.get_overseas_balance("US")

    assert balances[0].asset == "AAPL"
    assert balances[0].total == Decimal("10")
