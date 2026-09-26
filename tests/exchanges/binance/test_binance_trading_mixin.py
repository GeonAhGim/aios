"""task-7603(BR-22d) — BinanceTradingMixin 주문·취소·정정 테스트.

아직 BinanceAdapter 조립체가 없으므로, KIS/Bitget/Kiwoom trading_mixin
테스트와 동일한 관례로 믹스인 + 최소 스텁 클라이언트를 이 파일 안에서
직접 구성한다(D2 floor: 부정 테스트 ≥3, 장애주입 1, 성능 수치 단언 1,
D3: INVARIANTS I-02/I-03 대조 적대적 테스트 1 — 근거는 각 테스트
docstring 참고. replay_verify는 이 leaf가 DB/이벤트스토어에 쓰지 않는
순수 어댑터 메서드라 N/A — 해당 게이트는 executor/oms 경로에서 검증).
"""

from __future__ import annotations

import time
from decimal import Decimal
from typing import Any

import pytest

from src.core.exceptions import FatalExchangeError, FrozenZonePaperAdapterBlockedError
from src.data.models.base import AssetClass, Currency, Money
from src.data.models.trading import Order, OrderSide, OrderStatus, OrderType
from src.exchanges.binance.trading_mixin import BinanceTradingMixin

pytestmark = pytest.mark.asyncio


class _StubClient(BinanceTradingMixin):
    def __init__(
        self,
        *,
        demo_mode: bool,
        responses: dict[str, dict[str, Any]] | None = None,
        raise_path: str | None = None,
    ) -> None:
        self.is_paper_trading = demo_mode
        self.is_sandboxed = demo_mode
        self._responses = responses or {}
        self._raise_path = raise_path
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []

    async def _request(
        self, method: str, path: str, *, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        self.calls.append((method, path, params))
        if self._raise_path == path:
            raise AssertionError(f"가드가 막았어야 할 요청이 실제로 나갔습니다: {path}")
        return self._responses.get(path, {"orderId": 1234567, "status": "NEW"})

    async def get_order(self, order_id: str) -> Order:
        return _order().model_copy(
            update={"exchange_order_id": order_id, "status": OrderStatus.ACKNOWLEDGED}
        )


def _order(*, side: OrderSide = OrderSide.BUY, order_type: OrderType = OrderType.LIMIT) -> Order:
    return Order(
        client_order_id="c-1",
        strategy_id="s-1",
        strategy_version="v1",
        symbol="BTCUSDT",
        exchange="binance",
        side=side,
        order_type=order_type,
        quantity=Decimal("0.01"),
        price=None
        if order_type == OrderType.MARKET
        else Money(amount=Decimal("60000"), currency=Currency.USDT),
        asset_class=AssetClass.CRYPTO,
    )


def _live_client(raise_path: str | None = None) -> _StubClient:
    return _StubClient(demo_mode=False, raise_path=raise_path)


def _paper_client(**kwargs: Any) -> _StubClient:
    return _StubClient(demo_mode=True, **kwargs)


# ---- LIVE 하드가드 (D3 — INVARIANTS I-02/I-03 대조 적대적 테스트) ----


async def test_place_order_rejects_live_adapter():
    """demo_mode=False(LIVE)에서는 place_order가 HTTP 요청 전에 거부돼야
    한다 — 요청이 실제로 나가면 _request가 AssertionError로 실패시킨다."""
    client = _live_client(raise_path="/api/v3/order")
    with pytest.raises(FrozenZonePaperAdapterBlockedError):
        await client.place_order(_order())
    assert client.calls == []


async def test_cancel_order_rejects_live_adapter():
    client = _live_client(raise_path="/api/v3/order")
    with pytest.raises(FrozenZonePaperAdapterBlockedError):
        await client.cancel_order("BTCUSDT:1234567")
    assert client.calls == []


async def test_modify_order_rejects_live_adapter():
    client = _live_client(raise_path="/api/v3/order/cancelReplace")
    with pytest.raises(FrozenZonePaperAdapterBlockedError):
        await client.modify_order(
            "BTCUSDT:1234567",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=Decimal("0.02"),
            price=Decimal("61000"),
        )
    assert client.calls == []


# ---- 해피 패스 ----


async def test_place_order_buy_limit_sends_time_in_force_and_price():
    client = _paper_client(responses={"/api/v3/order": {"orderId": 1234567, "status": "NEW"}})
    result = await client.place_order(_order(side=OrderSide.BUY, order_type=OrderType.LIMIT))
    assert result.exchange_order_id == "BTCUSDT:1234567"
    assert result.status == OrderStatus.SUBMITTED
    method, path, params = client.calls[0]
    assert (method, path) == ("POST", "/api/v3/order")
    assert params is not None
    assert params["timeInForce"] == "GTC"
    assert params["price"] == "60000"


async def test_place_order_sell_market_omits_price_and_time_in_force():
    client = _paper_client(responses={"/api/v3/order": {"orderId": 7654321, "status": "NEW"}})
    result = await client.place_order(_order(side=OrderSide.SELL, order_type=OrderType.MARKET))
    _, _, params = client.calls[0]
    assert "price" not in params
    assert "timeInForce" not in params
    assert result.exchange_order_id == "BTCUSDT:7654321"


async def test_cancel_order_splits_composite_id_and_returns_true_on_success():
    client = _paper_client(responses={"/api/v3/order": {"status": "CANCELED"}})
    ok = await client.cancel_order("BTCUSDT:1234567")
    assert ok is True
    method, path, params = client.calls[0]
    assert (method, path) == ("DELETE", "/api/v3/order")
    assert params["symbol"] == "BTCUSDT"
    assert params["orderId"] == "1234567"


async def test_modify_order_uses_cancel_replace_and_delegates_to_get_order():
    client = _paper_client(
        responses={"/api/v3/order/cancelReplace": {"newOrderResponse": {"orderId": 9999999}}}
    )
    result = await client.modify_order(
        "BTCUSDT:1234567",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=Decimal("0.02"),
        price=Decimal("61000"),
    )
    method, path, params = client.calls[0]
    assert (method, path) == ("PUT", "/api/v3/order/cancelReplace")
    assert params["cancelOrderId"] == "1234567"
    assert params["cancelReplaceMode"] == "STOP_ON_FAILURE"
    assert params["price"] == "61000"
    assert result.exchange_order_id == "BTCUSDT:9999999"


# ---- 부정/장애주입 테스트 (D2 floor ≥3 부정 + 1 장애주입) ----


async def test_cancel_order_rejects_malformed_exchange_order_id():
    """부정 테스트 1: ':' 구분자가 없는 exchange_order_id는 FatalExchangeError."""
    client = _paper_client()
    with pytest.raises(FatalExchangeError):
        await client.cancel_order("not-a-composite-id")
    assert client.calls == []


async def test_modify_order_rejects_missing_required_field():
    """부정 테스트 2: Binance cancelReplace는 전체 대체 주문 형태(side/
    order_type/quantity)가 필수 — 수량만 바꾸려 해도 side/type 없이는
    거래소에 요청을 보내기 전에 거부한다(in-place amend가 없는 API
    특성)."""
    client = _paper_client()
    with pytest.raises(FatalExchangeError):
        await client.modify_order("BTCUSDT:1234567", quantity=Decimal("0.02"))
    assert client.calls == []


async def test_modify_order_limit_without_price_is_rejected():
    """부정 테스트 3: LIMIT 정정은 가격 없이 호출하면 거부한다(시장가
    fallback으로 조용히 넘어가지 않음)."""
    client = _paper_client()
    with pytest.raises(FatalExchangeError):
        await client.modify_order(
            "BTCUSDT:1234567",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=Decimal("0.02"),
        )
    assert client.calls == []


async def test_place_order_raises_fatal_exchange_error_on_missing_order_id():
    """부정 테스트 4 + 장애주입: 거래소 응답에 orderId 필드가 없는(스키마
    변경/장애) 경우, 설명 없는 KeyError 대신 FatalExchangeError로 통일해
    호출부가 원인을 알 수 있게 한다(Kiwoom trading_mixin과 동일 관례)."""
    client = _paper_client(responses={"/api/v3/order": {"status": "NEW"}})
    with pytest.raises(FatalExchangeError):
        await client.place_order(_order())


async def test_place_order_limit_without_price_is_rejected():
    """부정 테스트 5: place_order도 LIMIT 주문에 가격이 없으면 거래소
    호출 전에 거부한다."""
    client = _paper_client()
    order = _order(order_type=OrderType.LIMIT).model_copy(update={"price": None})
    with pytest.raises(FatalExchangeError):
        await client.place_order(order)
    assert client.calls == []


# ---- 성능 수치 단언 ----


@pytest.mark.perf
async def test_place_order_latency_budget():
    """숫자 성능 단언: place_order는 순수 스텁 클라이언트(네트워크 없음)
    기준 100회 호출 평균 1ms 미만이어야 한다 — 실거래소 왕복 시간이
    아니라, 믹스인 자체의 오버헤드(바디 조립·응답 파싱)에 대한 회귀
    가드다. 실거래소 p95/p99 레이턴시 예산은 조립체(adapter.py, 미구현)
    단계에서 계약 테스트로 별도 측정한다 — N/A(HTTP 클라이언트 미존재,
    account_mixin/auth 선행 필요)."""
    client = _paper_client(responses={"/api/v3/order": {"orderId": 1234567, "status": "NEW"}})
    order = _order()
    start = time.perf_counter()
    for _ in range(100):
        await client.place_order(order)
    elapsed = time.perf_counter() - start
    assert elapsed / 100 < 0.001
