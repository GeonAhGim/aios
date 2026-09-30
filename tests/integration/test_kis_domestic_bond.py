"""02d_kis_api_full_spec_v1.md §4 통합테스트 — 국내채권(domestic_bond).

httpx.MockTransport 기반 검증(test_kis_adapter.py와 동일 원칙).
"""

from decimal import Decimal

import httpx
import pytest

from src.core.exceptions import RetryableExchangeError
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
        lambda request: _route(request, {"/uapi/domestic-bond/v1/trading/inquire-balance": handler})
    )
    balances = await adapter.get_bond_balance()

    assert len(balances) == 1
    assert balances[0].asset == "KR2033022D33"
    assert balances[0].total == Decimal("10")


# ── negative tests: rt_cd != "0" (fail-closed 원칙, test_kis_overseas_stock.py와 동일 패턴) ──


async def test_get_bond_price_raises_on_api_error() -> None:
    """실패주입: 시세조회 API가 rt_cd != "0"으로 오류를 반환하면 예외가 난다."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"rt_cd": "1", "msg1": "존재하지 않는 종목코드", "output": {}}
        )

    adapter = _make_adapter(
        lambda request: _route(
            request, {"/uapi/domestic-bond/v1/quotations/inquire-price": handler}
        )
    )

    with pytest.raises(RetryableExchangeError):
        await adapter.get_bond_price("999999")


async def test_place_bond_order_raises_on_api_error() -> None:
    """실패주입: 주문 제출 API가 rt_cd != "0"으로 오류를 반환하면 예외가 난다."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"rt_cd": "1", "msg1": "주문 가능 수량 초과", "output": {}})

    adapter = _make_adapter(
        lambda request: _route(request, {"/uapi/domestic-bond/v1/trading/buy": handler})
    )
    order = _bond_order()

    with pytest.raises(RetryableExchangeError):
        await adapter.place_bond_order(order)


async def test_get_bond_balance_raises_on_api_error() -> None:
    """실패주입: 잔고조회 API가 rt_cd != "0"으로 오류를 반환하면 예외가 난다."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"rt_cd": "1", "msg1": "계좌 정보를 찾을 수 없습니다", "output": []}
        )

    adapter = _make_adapter(
        lambda request: _route(request, {"/uapi/domestic-bond/v1/trading/inquire-balance": handler})
    )

    with pytest.raises(RetryableExchangeError):
        await adapter.get_bond_balance()


# ── failure injection: 전송 계층 예외 ──────────────────────────────────────────


async def test_get_bond_price_propagates_transport_exception() -> None:
    """실패주입(task-4084 패턴): 연결 타임아웃은 재시도 후에도 실패하면
    RetryableExchangeError로 감싸져 호출자에게 전달된다."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/tokenP":
            return httpx.Response(200, json=TOKEN_RESPONSE)
        raise httpx.ConnectTimeout("connection refused")

    adapter = _make_adapter(handler)

    with pytest.raises(RetryableExchangeError):
        await adapter.get_bond_price("KR2033022D33")
