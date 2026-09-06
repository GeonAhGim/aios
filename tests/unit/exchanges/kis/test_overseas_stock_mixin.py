"""task-1782 BR-4 — 해외주식 거래소 전수 왕복 테스트.

ADR-2026-09-06-I D2: US가 NAS/NASD 하나로 뭉쳐 있던 결함을 고쳐 NYSE·AMEX를
비롯한 전 거래소를 커버한다. 두 코드 체계(시세조회 3자리 EXCD, 주문 4자리
OVRS_EXCG_CD)를 하나로 통일하지 않는다는 게 DoD의 핵심이라, 왕복 테스트는
거래소마다 두 코드가 실제로 다른 값으로 전송되는지, 시세코드를 주문에
쓰면 명시적으로 거부되는지를 함께 검증한다. `@require_paper_sandbox`가
막으므로 전부 모의투자(is_paper_trading=True) adapter로만 확인한다(D3).
"""
from __future__ import annotations

import json
from decimal import Decimal

import httpx
import pytest

from src.data.models.base import AssetClass
from src.data.models.trading import Order, OrderSide, OrderType
from src.exchanges.kis.adapter import KISAdapter
from src.exchanges.kis.overseas_stock_mixin import _EXCHANGES, _exchange_codes

_QUOTE_PATH = "/uapi/overseas-price/v1/quotations/price"
_ORDER_PATH = "/uapi/overseas-stock/v1/trading/order"
_CANCEL_PATH = "/uapi/overseas-stock/v1/trading/order-rvsecncl"
_BALANCE_PATH = "/uapi/overseas-stock/v1/trading/inquire-balance"


def _make_paper_adapter(captured: list[httpx.Request]) -> KISAdapter:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/oauth2/tokenP":
            return httpx.Response(200, json={"access_token": "t", "access_token_token_expired": ""})
        captured.append(request)
        if path == _QUOTE_PATH:
            output = {"last": "10.5", "tvol": "100"}
        elif path in (_ORDER_PATH, _CANCEL_PATH):
            output = {"KRX_FWDG_ORD_ORGNO": "ORG", "ODNO": "1"}
        elif path == _BALANCE_PATH:
            output = {}
        else:
            raise AssertionError(f"예상치 못한 경로: {path}")
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


def _market_order(exchange: str) -> Order:
    return Order(
        client_order_id="c-1",
        strategy_id="s-1",
        strategy_version="v1",
        symbol="AAPL",
        exchange="kis",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("1"),
        asset_class=AssetClass.US_EQUITY,
    )


@pytest.mark.parametrize("exchange", sorted(_EXCHANGES))
async def test_round_trip_per_exchange_uses_distinct_codes(exchange: str):
    """DoD: 거래소별 왕복(시세조회 → 주문 → 취소 → 잔고)이 모의투자에서
    통과하고, 시세코드(3자리)와 주문코드(4자리)가 섞이지 않는다."""
    codes = _exchange_codes(exchange)
    captured: list[httpx.Request] = []
    adapter = _make_paper_adapter(captured)

    ticker = await adapter.get_overseas_ticker("AAPL", exchange)
    assert ticker.price == Decimal("10.5")
    quote_request = captured[-1]
    assert quote_request.url.params["EXCD"] == codes.quote_excd

    order = await adapter.place_overseas_order(_market_order(exchange), exchange)
    assert order.exchange_order_id == "ORG:1"
    order_request = captured[-1]
    order_body = json.loads(order_request.content)
    assert order_body["OVRS_EXCG_CD"] == codes.order_excg_cd
    # 실전 tr_id는 T로 시작 — 모의투자는 V로 치환된다(_resolve_tr_id).
    assert order_request.headers["tr_id"] == "V" + codes.buy_tr_id[1:]

    cancelled = await adapter.cancel_overseas_order(
        "ORG:1", "AAPL", exchange, original_quantity=Decimal("1")
    )
    assert cancelled is True
    cancel_request = captured[-1]
    cancel_body = json.loads(cancel_request.content)
    assert cancel_body["OVRS_EXCG_CD"] == codes.order_excg_cd

    balances = await adapter.get_overseas_balance(exchange)
    assert balances == []
    balance_request = captured[-1]
    assert balance_request.url.params["OVRS_EXCG_CD"] == codes.order_excg_cd
    assert balance_request.url.params["TR_CRCY_CD"] == codes.currency

    # 시세코드(3자리)는 주문코드(4자리)와 절대 같은 문자열이 아니다.
    assert codes.quote_excd != codes.order_excg_cd


def test_unsupported_exchange_is_rejected_explicitly():
    with pytest.raises(ValueError, match="지원하지 않는 해외주식 거래소"):
        _exchange_codes("LSE")


async def test_place_order_rejects_unsupported_exchange_before_request():
    captured: list[httpx.Request] = []
    adapter = _make_paper_adapter(captured)

    with pytest.raises(ValueError, match="지원하지 않는 해외주식 거래소"):
        await adapter.place_overseas_order(_market_order("LSE"), "LSE")

    assert captured == []


@pytest.mark.parametrize(
    "quote_code", ["NAS", "NYS", "AMS", "HKS", "SHS", "SZS", "TSE", "HSX", "HNX"]
)
async def test_place_order_with_quote_code_fails(quote_code: str):
    """DoD negative — 시세조회용 3자리 코드로 주문하면 실패해야 한다(두
    코드 체계를 하나로 통일하지 않음을 보증)."""
    captured: list[httpx.Request] = []
    adapter = _make_paper_adapter(captured)

    with pytest.raises(ValueError, match="지원하지 않는 해외주식 거래소"):
        await adapter.place_overseas_order(_market_order(quote_code), quote_code)

    assert captured == []


def test_exchanges_cover_required_set():
    """ADR-2026-09-06-I D2 — US 하나로 뭉치지 않고 NYSE·AMEX를 포함한
    전 거래소가 열거돼 있는지 확인한다."""
    required = {
        "NASD",
        "NYSE",
        "AMEX",
        "SEHK",
        "SHAA",
        "SZAA",
        "TKSE",
        "VNSE",
        "HASE",
    }
    assert required <= set(_EXCHANGES)


def test_quote_and_order_codes_never_collide():
    """두 코드 체계를 하나로 통일하지 않는다 — 어떤 거래소도 3자리
    시세코드를 그대로 4자리 주문코드로 쓰지 않는다."""
    for codes in _EXCHANGES.values():
        assert codes.quote_excd != codes.order_excg_cd
