"""task-1784 BR-6 — 국내선물옵션(domestic_futureoption) 왕복·DoD 테스트.

DoD(task-1784): (1) 시세·주문·취소·잔고 왕복이 모의투자에서 통과, (2) 만기
종목 주문 거부, (3) 승수 반영 손익 계산이 수기 계산과 일치. `@require_paper_
sandbox`가 막으므로 매매 계열 호출은 전부 모의투자(is_paper_trading=True)
adapter로만 확인한다(ADR-2026-09-06-I D3).
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import httpx
import pytest

from src.data.models.base import AssetClass
from src.data.models.trading import Order, OrderSide, OrderStatus, OrderType
from src.exchanges.kis.adapter import KISAdapter
from src.exchanges.kis.domestic_futureoption_mixin import (
    ContractExpiredError,
    calculate_settlement_pnl,
)

_QUOTE_PATH = "/uapi/domestic-futureoption/v1/quotations/inquire-price"
_ORDER_PATH = "/uapi/domestic-futureoption/v1/trading/order"
_CANCEL_PATH = "/uapi/domestic-futureoption/v1/trading/order-rvsecncl"
_BALANCE_PATH = "/uapi/domestic-futureoption/v1/trading/inquire-balance"

_FAR_FUTURE_EXPIRY = date(2099, 12, 1)


def _today_kst() -> date:
    """`domestic_futureoption_mixin._reject_if_expired`와 동일하게 KST 달력일
    기준 — 로컬 머신 타임존과 무관하게 결정적인 경계값 테스트를 만든다."""
    return datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Seoul")).date()


def _make_paper_adapter(captured: list[httpx.Request]) -> KISAdapter:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/oauth2/tokenP":
            return httpx.Response(200, json={"access_token": "t", "access_token_token_expired": ""})
        captured.append(request)
        if path == _QUOTE_PATH:
            output = {"futs_prpr": "352.50", "acml_vol": "1000"}
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
        symbol="101W09",
        exchange="kis",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("1"),
        asset_class=AssetClass.KR_FUTURES,
        expiry_date=expiry_date,
        contract_multiplier=Decimal("250000"),
        underlying_symbol="KOSPI200",
    )


async def test_round_trip_quote_order_cancel_balance():
    """DoD 1 — 시세조회 → 주문 → 취소 → 잔고 왕복이 모의투자에서 통과한다."""
    captured: list[httpx.Request] = []
    adapter = _make_paper_adapter(captured)

    ticker = await adapter.get_futureoption_price("101W09")
    assert ticker.price == Decimal("352.50")
    quote_request = captured[-1]
    assert quote_request.url.params["FID_INPUT_ISCD"] == "101W09"

    order = await adapter.place_futureoption_order(_futures_order())
    assert order.exchange_order_id == "ORG:1"
    assert order.status == OrderStatus.SUBMITTED
    order_request = captured[-1]
    # 실전 tr_id(TTTO1101U)는 T로 시작 — 모의투자는 V로 치환된다.
    assert order_request.headers["tr_id"] == "VTTO1101U"
    order_body = json.loads(order_request.content)
    assert order_body["SHTN_PDNO"] == "101W09"
    assert order_body["SLL_BUY_DVSN_CD"] == "02"  # BUY

    cancelled = await adapter.cancel_futureoption_order("ORG:1", quantity=Decimal("1"))
    assert cancelled is True
    cancel_request = captured[-1]
    assert cancel_request.headers["tr_id"] == "VTTO1103U"

    balances = await adapter.get_futureoption_balance()
    assert balances == []
    balance_request = captured[-1]
    assert balance_request.headers["tr_id"] == "VTFO6118R"


async def test_place_order_wire_body_omits_ord_dvsn_cd_fails():
    """DoD(b)(task-2005 REJECT 근거) — ORD_DVSN_CD가 실제 HTTP 요청 바디에
    배선됐음을 증명한다. 이 필드가 코드에서 제거되면 이 assertion이 실패해야
    negative test로 유효하다(리뷰가 지적한 결함: 기존 테스트는 필드 제거해도
    전부 통과했다)."""
    captured: list[httpx.Request] = []
    adapter = _make_paper_adapter(captured)

    await adapter.place_futureoption_order(_futures_order())

    order_body = json.loads(captured[-1].content)
    assert "ORD_DVSN_CD" in order_body
    assert order_body["ORD_DVSN_CD"] == "01"  # MARKET 주문 → _order_division("01")


async def test_cancel_order_wire_body_omits_rmn_qty_yn_fails():
    """DoD(b) — RMN_QTY_YN이 실제 취소 요청 바디에 배선됐음을 증명한다.
    ORD_DVSN_CD와 동일한 근거로, 필드 제거 시 실패해야 하는 negative test다."""
    captured: list[httpx.Request] = []
    adapter = _make_paper_adapter(captured)

    await adapter.cancel_futureoption_order("ORG:1", quantity=Decimal("1"))

    cancel_body = json.loads(captured[-1].content)
    assert "RMN_QTY_YN" in cancel_body
    assert cancel_body["RMN_QTY_YN"] == "N"


async def test_place_order_rejects_expired_contract():
    """DoD 2 — 만기 지난 종목 주문은 거래소에 보내지 않고 거부한다."""
    captured: list[httpx.Request] = []
    adapter = _make_paper_adapter(captured)
    expired_order = _futures_order(expiry_date=_today_kst() - timedelta(days=1))

    with pytest.raises(ContractExpiredError):
        await adapter.place_futureoption_order(expired_order)

    assert captured == []  # 거래소에 아무 요청도 나가지 않아야 한다


async def test_place_order_allows_expiry_today():
    """만기 당일은 아직 유효한 거래일 — 오늘 날짜는 거부하지 않는다(경계값)."""
    captured: list[httpx.Request] = []
    adapter = _make_paper_adapter(captured)
    order_today_expiry = _futures_order(expiry_date=_today_kst())

    result = await adapter.place_futureoption_order(order_today_expiry)

    assert result.exchange_order_id == "ORG:1"


async def test_place_order_rejects_non_derivative_asset_class():
    """asset_class가 KR_FUTURES/KR_OPTION이 아니면 침묵 오분류를 막는다."""
    captured: list[httpx.Request] = []
    adapter = _make_paper_adapter(captured)
    wrong_class_order = _futures_order().model_copy(update={"asset_class": AssetClass.KR_EQUITY})

    with pytest.raises(ValueError, match="asset_class"):
        await adapter.place_futureoption_order(wrong_class_order)

    assert captured == []


def test_calculate_settlement_pnl_matches_manual_calculation():
    """DoD 3 — 승수 반영 손익 계산이 수기 계산과 일치한다.

    KOSPI200 선물(승수 250,000) 매수 2계약, 진입 350.00 → 청산 352.50:
    수기 계산 = (352.50 - 350.00) * 2 * 250,000 = 1,250,000원.
    """
    pnl = calculate_settlement_pnl(
        entry_price=Decimal("350.00"),
        exit_price=Decimal("352.50"),
        quantity=Decimal("2"),
        contract_multiplier=Decimal("250000"),
        side=OrderSide.BUY,
    )
    assert pnl == Decimal("1250000.00")


def test_calculate_settlement_pnl_sell_side_inverts_sign():
    """매도(숏) 포지션은 가격이 오르면 손실이어야 한다.

    수기 계산: (350.00 - 352.50) * 1 * 250,000 = -625,000원.
    """
    pnl = calculate_settlement_pnl(
        entry_price=Decimal("350.00"),
        exit_price=Decimal("352.50"),
        quantity=Decimal("1"),
        contract_multiplier=Decimal("250000"),
        side=OrderSide.SELL,
    )
    assert pnl == Decimal("-625000.00")
