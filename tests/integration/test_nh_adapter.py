"""NHAdapter 통합 테스트.

실제 NH API 키가 없는 상태라 httpx.MockTransport로 공식 SDK 소스코드
기준 요청 형태를 재현해 검증한다(test_kis_adapter.py와 동일 원칙).
응답 필드명은 02e_nh_api_spec_v1.md §3에 명시한 대로 미확인 최선
추정치라 실제 필드명이 다를 수 있음 — 여기서는 "그 추정 필드명이
있으면 정상 파싱되고, 없으면 FatalExchangeError로 실패한다"를 검증한다.
"""
import json
from decimal import Decimal

import httpx
import pytest

from src.core.exceptions import FatalExchangeError
from src.data.models.base import AssetClass
from src.data.models.trading import Order, OrderSide, OrderStatus, OrderType
from src.exchanges.nh.adapter import NHAdapter

TOKEN_RESPONSE = {"access_token": "tok-1", "expires_in": 86400}


def _make_adapter(handler, *, is_paper_trading: bool = True) -> NHAdapter:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(base_url="https://moapi.nhplug.com:8443", transport=transport)
    return NHAdapter(
        "appkey", "appsecret", "1234567890", is_paper_trading=is_paper_trading, http_client=client
    )


def _route(request: httpx.Request, routes: dict) -> httpx.Response:
    if request.url.path == "/oauth2/token":
        return httpx.Response(200, json=TOKEN_RESPONSE)
    handler = routes.get(request.url.path)
    assert handler is not None, f"no route for {request.url.path}"
    return handler(request)


def _success(output_0: dict | list | None = None, **extra_outputs) -> dict:
    body: dict = {"rsp_cd": "00000", "rsp_msg": "정상처리완료"}
    if output_0 is not None:
        body["Output_0"] = output_0
    body.update(extra_outputs)
    return body


def _order(**overrides) -> Order:
    defaults = dict(
        client_order_id="c-1",
        strategy_id="s-1",
        strategy_version="v1",
        symbol="005930",
        exchange="nh",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=Decimal("10"),
        asset_class=AssetClass.KR_EQUITY,
    )
    defaults.update(overrides)
    return Order(**defaults)


# ---------- token issuance ----------


async def test_ensure_token_sends_form_params():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/oauth2/token"
        assert request.url.params["appkey"] == "appkey"
        assert request.url.params["appsecretkey"] == "appsecret"
        assert request.url.params["grant_type"] == "client_credentials"
        return httpx.Response(200, json=TOKEN_RESPONSE)

    adapter = _make_adapter(handler)
    token = await adapter._ensure_token()

    assert token == "tok-1"


async def test_ensure_token_raises_fatal_on_non_200():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="unauthorized")

    adapter = _make_adapter(handler)
    with pytest.raises(FatalExchangeError):
        await adapter._ensure_token()


# ---------- request-level error handling ----------


async def test_request_raises_retryable_on_non_json_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return _route(
            request,
            {"/krstock/quote/v1/currentPrice": lambda r: httpx.Response(200, text="<html/>")},
        )

    adapter = _make_adapter(handler)
    from src.core.exceptions import RetryableExchangeError

    with pytest.raises(RetryableExchangeError):
        await adapter.get_ticker("005930")


async def test_request_raises_retryable_on_business_failure_code():
    def handler(request: httpx.Request) -> httpx.Response:
        return _route(
            request,
            {
                "/krstock/quote/v1/currentPrice": lambda r: httpx.Response(
                    200, json={"rsp_cd": "99999", "rsp_msg": "실패"}
                )
            },
        )

    from src.core.exceptions import RetryableExchangeError

    adapter = _make_adapter(handler)
    with pytest.raises(RetryableExchangeError):
        await adapter.get_ticker("005930")


async def test_request_treats_wanryo_message_as_success_even_with_unlisted_code():
    """SDK 관례 — rsp_cd가 알려진 성공코드 목록에 없어도 rsp_msg에
    "완료"가 포함되면 성공으로 취급한다(02e 스펙 §2)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return _route(
            request,
            {
                "/krstock/quote/v1/currentPrice": lambda r: httpx.Response(
                    200,
                    json={
                        "rsp_cd": "ZZZZZ",
                        "rsp_msg": "처리 완료",
                        "Output_0": {"prpr": "70000", "bidp": "69900", "askp": "70100"},
                    },
                )
            },
        )

    adapter = _make_adapter(handler)
    ticker = await adapter.get_ticker("005930")

    assert ticker.price == Decimal("70000")


# ---------- market data ----------


async def test_get_ticker_parses_output():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/krstock/quote/v1/currentPrice"
        body = json.loads(request.content)
        assert body["iem_cd"] == "005930"
        return httpx.Response(
            200, json=_success({"prpr": "70000", "bidp": "69900", "askp": "70100"})
        )

    adapter = _make_adapter(
        lambda request: _route(request, {"/krstock/quote/v1/currentPrice": handler})
    )
    ticker = await adapter.get_ticker("005930")

    assert ticker.price == Decimal("70000")
    assert ticker.bid == Decimal("69900")


async def test_get_ticker_raises_fatal_when_field_missing():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_success({"unexpected_field": "1"}))

    adapter = _make_adapter(
        lambda request: _route(request, {"/krstock/quote/v1/currentPrice": handler})
    )
    with pytest.raises(FatalExchangeError):
        await adapter.get_ticker("005930")


async def test_get_orderbook_parses_bid_ask():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=_success({"prpr": "70000", "bidp": "69900", "askp": "70100"})
        )

    adapter = _make_adapter(
        lambda request: _route(request, {"/krstock/quote/v1/currentPrice": handler})
    )
    book = await adapter.get_orderbook("005930")

    assert book.bids[0].price == Decimal("69900")
    assert book.asks[0].price == Decimal("70100")


async def test_get_orderbook_raises_fatal_when_no_quote_fields():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_success({"prpr": "70000"}))

    adapter = _make_adapter(
        lambda request: _route(request, {"/krstock/quote/v1/currentPrice": handler})
    )
    with pytest.raises(FatalExchangeError):
        await adapter.get_orderbook("005930")


async def test_get_ohlcv_not_implemented():
    adapter = _make_adapter(lambda request: httpx.Response(200, json=TOKEN_RESPONSE))
    with pytest.raises(NotImplementedError):
        await adapter.get_ohlcv("005930", "1d")


async def test_subscribe_ticker_stream_not_implemented():
    adapter = _make_adapter(lambda request: httpx.Response(200, json=TOKEN_RESPONSE))
    with pytest.raises(NotImplementedError):
        await adapter.subscribe_ticker_stream("005930", lambda t: None)


async def test_capabilities_declare_websocket_unsupported():
    adapter = _make_adapter(lambda request: httpx.Response(200, json=TOKEN_RESPONSE))
    caps = adapter.get_capabilities()

    assert caps.supported_asset_classes == [AssetClass.KR_EQUITY]
    assert caps.supports_websocket is False  # 데이터 메시지 포맷 미확인
    assert caps.market_hours is not None


# ---------- account ----------


async def test_get_balance_maps_holdings_and_cash():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/krstock/inquiry/v1/balance"
        body = json.loads(request.content)
        assert body["act_no"] == "1234567890"
        return httpx.Response(
            200,
            json=_success(
                Output_1=[{"iem_cd": "005930", "hld_qty": "10", "ord_psb_qty": "10"}],
                Output_2=[{"dpst_amt": "1000000"}],
            ),
        )

    adapter = _make_adapter(
        lambda request: _route(request, {"/krstock/inquiry/v1/balance": handler})
    )
    balances = await adapter.get_balance()

    assets = {b.asset: b.total for b in balances}
    assert assets["005930"] == Decimal("10")
    assert assets["KRW"] == Decimal("1000000")


async def test_get_positions_always_empty():
    adapter = _make_adapter(lambda request: httpx.Response(200, json=TOKEN_RESPONSE))
    assert await adapter.get_positions() == []


# ---------- trading ----------


async def test_place_order_buy_uses_cash_buy_endpoint():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/krstock/order/v1/cashBuy"
        body = json.loads(request.content)
        assert body["nmn_pr_tp_cd"] == "01"  # 지정가
        return httpx.Response(200, json=_success({"odno": "999"}))

    adapter = _make_adapter(
        lambda request: _route(request, {"/krstock/order/v1/cashBuy": handler})
    )
    order = _order()

    result = await adapter.place_order(order)

    assert result.exchange_order_id == "999"
    assert result.status == OrderStatus.SUBMITTED


async def test_place_order_sell_uses_cash_sell_endpoint():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/krstock/order/v1/cashSell"
        return httpx.Response(200, json=_success({"odno": "999"}))

    adapter = _make_adapter(
        lambda request: _route(request, {"/krstock/order/v1/cashSell": handler})
    )
    order = _order(side=OrderSide.SELL)

    await adapter.place_order(order)


async def test_place_order_market_type_uses_market_division_code():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["nmn_pr_tp_cd"] == "05"  # 시장가
        return httpx.Response(200, json=_success({"odno": "999"}))

    adapter = _make_adapter(
        lambda request: _route(request, {"/krstock/order/v1/cashBuy": handler})
    )
    order = _order(order_type=OrderType.MARKET, price=None)

    await adapter.place_order(order)


async def test_place_order_raises_fatal_when_field_missing():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_success({"unexpected": "1"}))

    adapter = _make_adapter(
        lambda request: _route(request, {"/krstock/order/v1/cashBuy": handler})
    )
    with pytest.raises(FatalExchangeError):
        await adapter.place_order(_order())


async def test_cancel_order_returns_true_on_success():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/krstock/order/v1/cashCancel"
        body = json.loads(request.content)
        assert body["orgn_odno"] == "999"
        return httpx.Response(200, json=_success())

    adapter = _make_adapter(
        lambda request: _route(request, {"/krstock/order/v1/cashCancel": handler})
    )
    assert await adapter.cancel_order("999") is True


async def test_is_paper_trading_and_sandboxed_are_always_false():
    """task-106 재확인 — 모의투자 도메인이 공식 문서상 "미제공"이라
    확인되기 전까지 항상 False(생성자 플래그와 무관, adapter.py 참조).
    Executor의 이중 검사(mode!=PAPER + 이 두 프로퍼티)가 이 신호로
    이 adapter의 실거래를 차단하는 것이 의도된 동작이다."""
    adapter = _make_adapter(
        lambda request: httpx.Response(200, json=TOKEN_RESPONSE), is_paper_trading=True
    )
    assert adapter.is_paper_trading is False
    assert adapter.is_sandboxed is False


async def test_modify_order_calls_cash_modify_then_reconfirms():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/krstock/order/v1/cashModify":
            return httpx.Response(200, json=_success())
        assert request.url.path == "/krstock/inquiry/v1/orderHistory"
        return httpx.Response(
            200,
            json=_success(
                [
                    {
                        "iem_cd": "005930",
                        "orr_qty": "10",
                        "ccld_qty": "0",
                        "ssl_byv_dit_cd": "02",
                    }
                ]
            ),
        )

    adapter = _make_adapter(
        lambda request: _route(
            request,
            {
                "/krstock/order/v1/cashModify": handler,
                "/krstock/inquiry/v1/orderHistory": handler,
            },
        )
    )
    order = await adapter.modify_order("999", price=Decimal("71000"))

    assert calls == ["/krstock/order/v1/cashModify", "/krstock/inquiry/v1/orderHistory"]
    assert order.exchange_order_id == "999"


async def test_get_order_not_found_raises_fatal():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_success([]))

    adapter = _make_adapter(
        lambda request: _route(request, {"/krstock/inquiry/v1/orderHistory": handler})
    )
    with pytest.raises(FatalExchangeError):
        await adapter.get_order("999")


async def test_get_order_parses_status():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_success(
                [
                    {
                        "iem_cd": "005930",
                        "orr_qty": "10",
                        "ccld_qty": "10",
                        "ssl_byv_dit_cd": "02",
                    }
                ]
            ),
        )

    adapter = _make_adapter(
        lambda request: _route(request, {"/krstock/inquiry/v1/orderHistory": handler})
    )
    order = await adapter.get_order("999")

    assert order.status == OrderStatus.FILLED
    assert order.side == OrderSide.BUY


# ---------- health check ----------


async def test_health_check_true_on_success():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_success(Output_1=[], Output_2=[]))

    adapter = _make_adapter(
        lambda request: _route(request, {"/krstock/inquiry/v1/balance": handler})
    )
    assert await adapter.health_check() is True


async def test_health_check_false_on_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"rsp_cd": "99999", "rsp_msg": "실패"})

    adapter = _make_adapter(
        lambda request: _route(request, {"/krstock/inquiry/v1/balance": handler})
    )
    assert await adapter.health_check() is False
