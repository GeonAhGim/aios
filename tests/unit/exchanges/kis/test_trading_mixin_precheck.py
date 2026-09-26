"""task-8074(AUDIT F4/§1) — KISTradingMixin.place_order()에 tick/lot/min
notional 사전검증이 전혀 없던 결함(task-7978 감사 발견)을 고정한다.

Spec: docs/audits/AUDIT_2026-09-26_order_path.md F4/§1,
docs/specs/L4_execution_oms_and_exchange_v1.0.md §2-A(R7).

`KIS_KR_EQUITY_PROFILE`(exchanges/kis/venue_profile.py)은 005930(삼성전자)에
대해 tick=100, lot=1, min_notional=0을 캐노니컬 키("005930.KS")로 선언한다.
실제 주문 경로(order_dispatch.py)는 venue 심볼("005930")을 그대로 넘기므로
`_lookup_symbol_spec`이 두 표기를 모두 시도한다 — 이 테스트는 그 경로가 실제로
거래소 호출 전에 거부하는지, 위반 시 HTTP 요청이 전혀 나가지 않는지(호출 카운터로
증명), 정상 주문은 회귀 없이 그대로 나가는지를 검증한다.
"""

from __future__ import annotations

from collections.abc import Callable, Generator
from decimal import Decimal

import httpx
import pytest

from src.data.models.base import AssetClass, Currency, Money
from src.data.models.trading import Order, OrderSide, OrderStatus, OrderType
from src.exchanges.kis import rate_profile
from src.exchanges.kis.adapter import KISAdapter
from src.services.oms.domain.errors import OrderValidationError

_TOKEN_PATH = "/oauth2/tokenP"
_TOKEN_RESPONSE = {"access_token": "t", "access_token_token_expired": "2099-01-01 00:00:00"}
_ORDER_CASH_PATH = "/uapi/domestic-stock/v1/trading/order-cash"


@pytest.fixture(autouse=True)
def _reset_bucket_registry() -> Generator[None, None, None]:
    """BR-2b — process-wide TokenBucket 싱글턴을 테스트 간 격리한다
    (test_task1011_protocol_mixins_deepen.py와 동일 이유)."""
    rate_profile.reset_token_bucket_registry_for_test()
    yield
    rate_profile.reset_token_bucket_registry_for_test()


def _make_adapter(
    handler: Callable[[httpx.Request], httpx.Response],
) -> KISAdapter:
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(
        base_url="https://openapivts.koreainvestment.com:29443", transport=transport
    )
    return KISAdapter(
        "app", "secret", "12345678", "01", is_paper_trading=True, http_client=http_client
    )


def _order(
    *,
    price: Decimal | None,
    quantity: Decimal,
    order_type: OrderType = OrderType.LIMIT,
    symbol: str = "005930",
) -> Order:
    return Order(
        client_order_id="c-1",
        strategy_id="s-1",
        strategy_version="v1",
        symbol=symbol,
        exchange="kis",
        side=OrderSide.BUY,
        order_type=order_type,
        quantity=quantity,
        price=Money(amount=price, currency=Currency.KRW) if price is not None else None,
        asset_class=AssetClass.KR_EQUITY,
    )


def _order_cash_handler(calls: list[httpx.Request]) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == _TOKEN_PATH:
            return httpx.Response(200, json=_TOKEN_RESPONSE)
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "rt_cd": "0",
                "msg1": "ok",
                "output": {"KRX_FWDG_ORD_ORGNO": "01234", "ODNO": "0000001"},
            },
        )

    return handler


# ---------------------------------------------------------------------------
# negative (>=3) — 사전검증 위반은 거래소 호출 없이 거부돼야 한다
# ---------------------------------------------------------------------------


async def test_place_order_rejects_tick_misaligned_price_without_calling_exchange() -> None:
    """005930 tick=100 — 70050(100의 배수가 아님)은 거부돼야 한다."""
    calls: list[httpx.Request] = []
    adapter = _make_adapter(_order_cash_handler(calls))
    order = _order(price=Decimal("70050"), quantity=Decimal("10"))

    with pytest.raises(OrderValidationError) as exc_info:
        await adapter.place_order(order)

    assert exc_info.value.reason == "TICK_MISALIGNED"
    assert calls == []  # 거래소로 나간 order-cash 요청이 전혀 없어야 함


async def test_place_order_rejects_lot_misaligned_quantity_without_calling_exchange() -> None:
    """005930 lot=1(정수 단위) — 소수 수량은 거부돼야 한다."""
    calls: list[httpx.Request] = []
    adapter = _make_adapter(_order_cash_handler(calls))
    order = _order(price=Decimal("70000"), quantity=Decimal("1.5"))

    with pytest.raises(OrderValidationError) as exc_info:
        await adapter.place_order(order)

    assert exc_info.value.reason == "LOT_MISALIGNED"
    assert calls == []


async def test_place_order_rejects_below_min_notional_without_calling_exchange() -> None:
    """min_notional을 임의로 요구하는 심볼(005930 스냅샷 재사용, 값만 다르게
    주입할 수 없어 venue_profile을 직접 monkeypatch)에서 주문가치 미달이면
    거부돼야 한다."""
    calls: list[httpx.Request] = []
    adapter = _make_adapter(_order_cash_handler(calls))

    original_profile = adapter.venue_profile()
    patched_profile = original_profile.model_copy(
        update={"min_notional": {"005930.KS": Decimal("1000000")}}
    )
    adapter.venue_profile = lambda: patched_profile  # type: ignore[method-assign]

    order = _order(price=Decimal("70000"), quantity=Decimal("1"))  # notional=70000 < 1,000,000

    with pytest.raises(OrderValidationError) as exc_info:
        await adapter.place_order(order)

    assert exc_info.value.reason == "MIN_NOTIONAL"
    assert calls == []


# ---------------------------------------------------------------------------
# failure-injection — 사전검증 예외가 place_order 밖으로 그대로 전파돼야 한다
# (호출부가 SUBMITTED로 위장하지 않는지)
# ---------------------------------------------------------------------------


async def test_place_order_precheck_failure_does_not_return_a_submitted_order() -> None:
    calls: list[httpx.Request] = []
    adapter = _make_adapter(_order_cash_handler(calls))
    order = _order(price=Decimal("70050"), quantity=Decimal("10"))

    try:
        result = await adapter.place_order(order)
    except OrderValidationError:
        result = None

    assert result is None
    assert calls == []


# ---------------------------------------------------------------------------
# 회귀 없음 — tick/lot에 정렬되고 min_notional을 만족하는 정상 주문은 그대로
# 거래소로 나가야 한다
# ---------------------------------------------------------------------------


async def test_place_order_accepts_aligned_order_and_calls_exchange() -> None:
    calls: list[httpx.Request] = []
    adapter = _make_adapter(_order_cash_handler(calls))
    order = _order(price=Decimal("70000"), quantity=Decimal("10"))  # tick=100 배수, lot=1 배수

    result = await adapter.place_order(order)

    assert len(calls) == 1
    assert result.status == OrderStatus.SUBMITTED
    assert result.exchange_order_id == "01234:0000001"


async def test_place_order_accepts_market_order_without_price_check() -> None:
    """시장가 주문(price=None)은 tick/min_notional 검사 대상이 아니다 —
    lot 검사만 적용되고, lot=1을 만족하면 그대로 나가야 한다."""
    calls: list[httpx.Request] = []
    adapter = _make_adapter(_order_cash_handler(calls))
    order = _order(price=None, quantity=Decimal("5"), order_type=OrderType.MARKET)

    result = await adapter.place_order(order)

    assert len(calls) == 1
    assert result.status == OrderStatus.SUBMITTED
