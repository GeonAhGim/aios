"""task-1785 BR-7 — 해외선물옵션(overseas_futureoption) 요청조립·응답파싱 테스트.

DoD(task-1785): (1) 시세·주문·취소·잔고 요청 조립/응답 파싱이 mock transport로
통과, (2) 만기 지난 종목 주문 거부, (3) asset_class 오분류 거부. `@require_paper_
sandbox`가 막으므로 매매 계열 호출은 전부 모의투자(is_paper_trading=True)
adapter로만 확인한다(ADR-2026-09-06-I D3) — 단, overseas_futureoption_mixin.py
docstring에 적었듯 이 도메인 전체는 kis_tr_reference.json에 V-접두 대응 TR이
하나도 없어(구조적 증거, 미검증) 실제 KIS 모의투자 서버가 이 tr_id들을
처리하는지는 이 mock 테스트가 증명하지 못한다 — 우리 코드의 요청 조립/응답
파싱 경로만 검증한다. 실계좌 확보 후 실서버 왕복 검증은 별도 리프로 남는다.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pytest

from src.data.models.base import AssetClass
from src.data.models.trading import Order, OrderSide, OrderStatus, OrderType
from src.exchanges.kis.adapter import KISAdapter
from src.exchanges.kis.overseas_futureoption_mixin import OverseasContractExpiredError

_QUOTE_FUTURES_PATH = "/uapi/overseas-futureoption/v1/quotations/inquire-price"
_QUOTE_OPTION_PATH = "/uapi/overseas-futureoption/v1/quotations/opt-price"
_ORDER_PATH = "/uapi/overseas-futureoption/v1/trading/order"
_CANCEL_PATH = "/uapi/overseas-futureoption/v1/trading/order-rvsecncl"
_BALANCE_PATH = "/uapi/overseas-futureoption/v1/trading/inquire-unpd"

_FAR_FUTURE_EXPIRY = date(2099, 12, 1)


def _today_utc() -> date:
    """`overseas_futureoption_mixin._reject_if_expired`와 동일 기준(UTC 달력일)."""
    return datetime.now(timezone.utc).date()


def _make_paper_adapter(captured: list[httpx.Request]) -> KISAdapter:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/oauth2/tokenP":
            return httpx.Response(200, json={"access_token": "t", "access_token_token_expired": ""})
        captured.append(request)
        if path in (_QUOTE_FUTURES_PATH, _QUOTE_OPTION_PATH):
            output = {"last": "4521.50", "tvol": "12345"}
        elif path in (_ORDER_PATH, _CANCEL_PATH):
            output = {"KRX_FWDG_ORD_ORGNO": "ORG", "ODNO": "1"}
        else:
            output = {}
        payload: dict[str, object] = {"rt_cd": "0", "msg1": "OK", "output": output}
        if path == _BALANCE_PATH:
            payload["output1"] = []
        return httpx.Response(200, json=payload)

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(
        base_url="https://openapivts.koreainvestment.com:29443", transport=transport
    )
    return KISAdapter(
        "app", "secret", "12345678", "01", is_paper_trading=True, http_client=http_client
    )


def _futures_order(*, expiry_date: date | None = _FAR_FUTURE_EXPIRY) -> Order:
    return Order(
        client_order_id="c-1",
        strategy_id="s-1",
        strategy_version="v1",
        symbol="ESZ26",
        exchange="kis",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("1"),
        asset_class=AssetClass.OVERSEAS_FUTURES,
        expiry_date=expiry_date,
        contract_multiplier=Decimal("50"),
        underlying_symbol="ES",
    )


async def test_round_trip_quote_order_cancel_balance():
    """DoD 1 — 시세조회→주문→취소→잔고 요청조립/응답파싱이 mock transport로 통과한다."""
    captured: list[httpx.Request] = []
    adapter = _make_paper_adapter(captured)

    ticker = await adapter.get_overseas_futureoption_price("ESZ26")
    assert ticker.price == Decimal("4521.50")
    quote_request = captured[-1]
    assert quote_request.url.params["SYMB"] == "ESZ26"
    assert quote_request.url.path == _QUOTE_FUTURES_PATH
    # 시세 tr_id(H접두)는 T/J/C가 아니므로 모의투자 치환 대상이 아니다.
    assert quote_request.headers["tr_id"] == "HHDFC55010000"

    order = await adapter.place_overseas_futureoption_order(_futures_order())
    assert order.exchange_order_id == "ORG:1"
    assert order.status == OrderStatus.SUBMITTED
    order_request = captured[-1]
    # OTFM 접두는 T/J/C가 아니므로 모의투자에서도 치환되지 않는다(도메인 전체가
    # V-접두 짝이 없다 — 모듈 docstring 참조, 실서버 처리 여부는 미검증).
    assert order_request.headers["tr_id"] == "OTFM3001U"
    order_body = json.loads(order_request.content)
    assert order_body["PDNO"] == "ESZ26"
    assert order_body["SLL_BUY_DVSN_CD"] == "02"  # BUY

    cancelled = await adapter.cancel_overseas_futureoption_order("ORG:1", quantity=Decimal("1"))
    assert cancelled is True
    cancel_request = captured[-1]
    assert cancel_request.headers["tr_id"] == "OTFM3003U"

    balances = await adapter.get_overseas_futureoption_balance()
    assert balances == []
    balance_request = captured[-1]
    assert balance_request.headers["tr_id"] == "OTFM1412R"
    assert balance_request.url.path == _BALANCE_PATH


async def test_get_option_price_uses_option_tr_id():
    """is_option=True는 선물이 아니라 옵션 tr_id(HHDFO55010000)를 써야 한다."""
    captured: list[httpx.Request] = []
    adapter = _make_paper_adapter(captured)

    await adapter.get_overseas_futureoption_price("ESZ26C4500", is_option=True)

    assert captured[-1].headers["tr_id"] == "HHDFO55010000"
    assert captured[-1].url.path == _QUOTE_OPTION_PATH


async def test_place_order_rejects_expired_contract():
    """DoD 2 — 만기 지난 종목 주문은 거래소에 보내지 않고 거부한다."""
    captured: list[httpx.Request] = []
    adapter = _make_paper_adapter(captured)
    expired_order = _futures_order(expiry_date=_today_utc() - timedelta(days=1))

    with pytest.raises(OverseasContractExpiredError):
        await adapter.place_overseas_futureoption_order(expired_order)

    assert captured == []  # 거래소에 아무 요청도 나가지 않아야 한다


async def test_place_order_allows_expiry_today():
    """만기 당일은 아직 유효한 거래일 — 오늘 날짜는 거부하지 않는다(경계값)."""
    captured: list[httpx.Request] = []
    adapter = _make_paper_adapter(captured)
    order_today_expiry = _futures_order(expiry_date=_today_utc())

    result = await adapter.place_overseas_futureoption_order(order_today_expiry)

    assert result.exchange_order_id == "ORG:1"


async def test_place_order_rejects_non_derivative_asset_class():
    """DoD 3 — asset_class가 OVERSEAS_FUTURES/OVERSEAS_OPTION이 아니면 침묵
    오분류를 막는다."""
    captured: list[httpx.Request] = []
    adapter = _make_paper_adapter(captured)
    wrong_class_order = _futures_order().model_copy(update={"asset_class": AssetClass.KR_EQUITY})

    with pytest.raises(ValueError, match="asset_class"):
        await adapter.place_overseas_futureoption_order(wrong_class_order)

    assert captured == []
