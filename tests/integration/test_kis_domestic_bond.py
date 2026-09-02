"""02d_kis_api_full_spec_v1.md §4 통합테스트 — 국내채권(domestic_bond).

httpx.MockTransport 기반 검증(test_kis_adapter.py와 동일 원칙).
"""
from decimal import Decimal

import httpx

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


def _bond_order(**overrides) -> Order:
    defaults = dict(
        client_order_id="c-1",
        strategy_id="s-1",
        strategy_version="v1",
        symbol="KR2033022D33",
        exchange="kis",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=Decimal("10"),
        asset_class=AssetClass.KR_EQUITY,
    )
    defaults.update(overrides)
    return Order(**defaults)


async def test_get_bond_price_parses_output():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["tr_id"] == "FHKBJ773400C0"
        assert request.url.params["FID_INPUT_ISCD"] == "KR2033022D33"
        return httpx.Response(
            200, json={"rt_cd": "0", "msg1": "ok", "output": {"bond_prpr": "10250"}}
        )

    adapter = _make_adapter(
        lambda request: _route(
            request, {"/uapi/domestic-bond/v1/quotations/inquire-price": handler}
        )
    )
    ticker = await adapter.get_bond_price("KR2033022D33")

    assert ticker.price == Decimal("10250")


async def test_place_bond_order_buy_uses_buy_endpoint_and_tr_id():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["tr_id"] == "VTTC0952U"  # 모의투자 치환 확인
        return httpx.Response(
            200,
            json={
                "rt_cd": "0",
                "msg1": "ok",
                "output": {"KRX_FWDG_ORD_ORGNO": "1234", "ODNO": "999"},
            },
        )

    adapter = _make_adapter(
        lambda request: _route(request, {"/uapi/domestic-bond/v1/trading/buy": handler})
    )
    order = _bond_order()

    result = await adapter.place_bond_order(order)

    assert result.exchange_order_id == "1234:999"
    assert result.status == OrderStatus.SUBMITTED


async def test_place_bond_order_sell_uses_sell_endpoint_and_tr_id():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["tr_id"] == "VTTC0958U"  # 모의투자 치환 확인
        return httpx.Response(
            200,
            json={
                "rt_cd": "0",
                "msg1": "ok",
                "output": {"KRX_FWDG_ORD_ORGNO": "1234", "ODNO": "999"},
            },
        )

    adapter = _make_adapter(
        lambda request: _route(request, {"/uapi/domestic-bond/v1/trading/sell": handler})
    )
    order = _bond_order(side=OrderSide.SELL)

    await adapter.place_bond_order(order)


async def test_get_bond_balance_filters_zero_quantity():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["tr_id"] == "VTSC8407R"  # 모의투자 치환 확인(C -> V)
        return httpx.Response(
            200,
            json={
                "rt_cd": "0",
                "msg1": "ok",
                "output": [
                    {"pdno": "KR2033022D33", "bal_qty": "10"},
                    {"pdno": "KR9999999999", "bal_qty": "0"},
                ],
            },
        )

    adapter = _make_adapter(
        lambda request: _route(
            request, {"/uapi/domestic-bond/v1/trading/inquire-balance": handler}
        )
    )
    balances = await adapter.get_bond_balance()

    assert len(balances) == 1
    assert balances[0].asset == "KR2033022D33"
    assert balances[0].total == Decimal("10")
