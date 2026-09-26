"""task-8077 F5(안정화 감사 정정) — KIS place_order()가 order.symbol을 PDNO로
전달하기 전 LA-7 단일 규칙(symbol_normalizer)을 경유해 KRX 6자리 코드 형식을
검증한다. 미등록·형식 불일치 심볼은 거래소 호출 전에 SymbolNormalizationError로
fail-closed 거부돼야 한다(Bitget _to_bitget_symbol 선례와 동일 계약).
"""

from __future__ import annotations

import json
import time
from decimal import Decimal

import httpx
import pytest

from src.data.models.base import AssetClass
from src.data.models.trading import Order, OrderSide, OrderStatus, OrderType
from src.exchanges.kis.adapter import KISAdapter
from src.foundation.market_data.contracts.v1 import Venue
from src.foundation.market_data.domain.reference.symbol_normalizer import (
    SymbolNormalizationError,
    to_venue,
)

_ORDER_PATH = "/uapi/domestic-stock/v1/trading/order-cash"


def _make_paper_adapter(captured: list[httpx.Request]) -> KISAdapter:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/oauth2/tokenP":
            return httpx.Response(200, json={"access_token": "t", "access_token_token_expired": ""})
        captured.append(request)
        output = {"KRX_FWDG_ORD_ORGNO": "ORG", "ODNO": "1"}
        return httpx.Response(200, json={"rt_cd": "0", "msg1": "OK", "output": output})

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(
        base_url="https://openapivts.koreainvestment.com:29443", transport=transport
    )
    return KISAdapter(
        "app", "secret", "12345678", "01", is_paper_trading=True, http_client=http_client
    )


def _stock_order(*, symbol: str) -> Order:
    return Order(
        client_order_id="c-1",
        strategy_id="s-1",
        strategy_version="v1",
        symbol=symbol,
        exchange="kis",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("1"),
        asset_class=AssetClass.KR_EQUITY,
    )


async def test_place_order_accepts_valid_krx_code():
    """회귀 없음 — 정상 6자리 KRX 코드는 그대로 통과하고 PDNO에 실린다."""
    captured: list[httpx.Request] = []
    adapter = _make_paper_adapter(captured)

    order = await adapter.place_order(_stock_order(symbol="005930"))

    assert order.exchange_order_id == "ORG:1"
    assert order.status == OrderStatus.SUBMITTED
    order_body = json.loads(captured[-1].content)
    assert order_body["PDNO"] == "005930"


@pytest.mark.parametrize(
    "bad_symbol",
    ["BTC/USDT", "AAPL", "12345", "0059300", ""],
)
async def test_place_order_rejects_unregistered_symbol(bad_symbol: str) -> None:
    """negative — KRX 6자리 코드 형식이 아닌 심볼은 거래소를 부르지 않고
    SymbolNormalizationError로 거부한다(fail-closed)."""
    captured: list[httpx.Request] = []
    adapter = _make_paper_adapter(captured)

    with pytest.raises(SymbolNormalizationError):
        await adapter.place_order(_stock_order(symbol=bad_symbol))

    assert captured == []  # 거래소에 아무 요청도 나가지 않아야 한다


async def test_place_order_rejects_lowercase_code() -> None:
    """negative — 대소문자·자릿수 혼동으로 우회하지 못한다(영문 혼입 코드)."""
    captured: list[httpx.Request] = []
    adapter = _make_paper_adapter(captured)

    with pytest.raises(SymbolNormalizationError):
        await adapter.place_order(_stock_order(symbol="00593a"))

    assert captured == []


@pytest.mark.perf
def test_krx_symbol_validation_meets_pretrade_gate_budget() -> None:
    """성능 단언(D2) — ADR-2026-09-09-C Decision 1의 사전거래 게이트 예산은
    p99 5ms. `to_venue(KIS_KRX, ...)`는 place_order()가 PDNO 조립 전에 매
    주문마다 거치는 사전거래 게이트이므로 그 예산 안에 들어야 한다."""
    samples: list[float] = []
    for _ in range(200):
        start = time.perf_counter()
        to_venue(Venue.KIS_KRX, "005930")
        samples.append(time.perf_counter() - start)

    samples.sort()
    p99 = samples[int(len(samples) * 0.99) - 1]
    assert p99 < 0.005
