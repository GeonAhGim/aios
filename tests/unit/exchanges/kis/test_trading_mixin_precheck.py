"""task-8074(AUDIT F4/§1) — KISTradingMixin.place_order()에 tick/lot/min
notional 사전검증이 전혀 없던 결함(task-7978 감사 발견)을 고정한다.

task-8337(리뷰 task-8182 REJECT 후속) — `_precheck_order`가 `VenueCapabilityProfile`
딕셔너리를 직접 읽어 `SymbolRegistry.spec()`/`require_verified`를 우회하던 결함을
고정한다. 프로덕션 KIS 스냅샷(`exchanges/kis/venue_profile.py`)의 005930.KS는
`verified=False`(가격대별 step table 미재확인)로 등록돼 있어, 이제 그 심볼로
주문하면 `_precheck_order`가 tick/lot/min_notional 판정 전에 거부해야 한다 —
기존 tick/lot/min_notional 회귀 테스트는 `verified=True` 테스트 전용
`SymbolRegistry`를 `adapter.symbol_registry`에 주입해 그 판정 로직 자체를
그대로 검증한다.

Spec: docs/audits/AUDIT_2026-09-26_order_path.md F4/§1,
docs/specs/L4_execution_oms_and_exchange_v1.0.md §2-A(R7), §9 L4-04 DoD c.
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
from src.services.oms.domain.symbol_registry import SymbolRegistry

_TOKEN_PATH = "/oauth2/tokenP"
_TOKEN_RESPONSE = {"access_token": "t", "access_token_token_expired": "2099-01-01 00:00:00"}
_ORDER_CASH_PATH = "/uapi/domestic-stock/v1/trading/order-cash"
_CANONICAL = "005930.KS"
_VENUE_SYMBOL = "005930"


def _verified_registry(
    *,
    tick: Decimal = Decimal("100"),
    lot: Decimal = Decimal("1"),
    min_notional: Decimal = Decimal("0"),
) -> SymbolRegistry:
    """tick/lot/min_notional 판정 로직 자체를 검증하는 테스트용
    `verified=True` 레지스트리 — 프로덕션 005930.KS 스냅샷은
    `verified=False`라 판정 로직 회귀 테스트에는 쓸 수 없다(그 자체가
    이 리프의 수정 대상)."""
    registry = SymbolRegistry()
    registry.register(
        _CANONICAL,
        "kis",
        _VENUE_SYMBOL,
        tick=tick,
        lot=lot,
        min_notional=min_notional,
        quote_ccy="KRW",
        verified=True,
    )
    return registry


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
    adapter.symbol_registry = _verified_registry
    order = _order(price=Decimal("70050"), quantity=Decimal("10"))

    with pytest.raises(OrderValidationError) as exc_info:
        await adapter.place_order(order)

    assert exc_info.value.reason == "TICK_MISALIGNED"
    assert calls == []  # 거래소로 나간 order-cash 요청이 전혀 없어야 함


async def test_place_order_rejects_lot_misaligned_quantity_without_calling_exchange() -> None:
    """005930 lot=1(정수 단위) — 소수 수량은 거부돼야 한다."""
    calls: list[httpx.Request] = []
    adapter = _make_adapter(_order_cash_handler(calls))
    adapter.symbol_registry = _verified_registry
    order = _order(price=Decimal("70000"), quantity=Decimal("1.5"))

    with pytest.raises(OrderValidationError) as exc_info:
        await adapter.place_order(order)

    assert exc_info.value.reason == "LOT_MISALIGNED"
    assert calls == []


async def test_place_order_rejects_below_min_notional_without_calling_exchange() -> None:
    """min_notional을 임의로 요구하는 심볼(005930 스냅샷 재사용, 값만 다르게
    주입한 verified=True 테스트 레지스트리)에서 주문가치 미달이면 거부돼야
    한다."""
    calls: list[httpx.Request] = []
    adapter = _make_adapter(_order_cash_handler(calls))
    adapter.symbol_registry = lambda: _verified_registry(min_notional=Decimal("1000000"))

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
    adapter.symbol_registry = _verified_registry
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
    adapter.symbol_registry = _verified_registry
    order = _order(price=None, quantity=Decimal("5"), order_type=OrderType.MARKET)

    result = await adapter.place_order(order)

    assert len(calls) == 1
    assert result.status == OrderStatus.SUBMITTED


# ---------------------------------------------------------------------------
# negative (verified=False) — task-8337(리뷰 task-8182 REJECT 후속): 프로덕션
# 스냅샷의 verified=False 심볼은 tick 판정 전에 명시적으로 거부돼야 한다
# ---------------------------------------------------------------------------


async def test_place_order_rejects_unverified_symbol_spec_without_calling_exchange() -> None:
    """프로덕션 KIS 심볼 레지스트리(주입 없음)의 005930.KS는 `verified=False`
    (KRX 가격대별 tick step table이 라이브로 재확인되지 않음, exchanges/kis/
    venue_profile.py 참조)로 등록돼 있다 — tick=100이 현재 가격대에서는
    맞더라도, 저가/고가 구간에서 오탐 거부 또는 실제 tick 위반 누락이
    가능하므로 `require_verified()`가 tick/lot/min_notional 판정 자체를
    거부해야 한다(§9 L4-04 DoD c)."""
    calls: list[httpx.Request] = []
    adapter = _make_adapter(_order_cash_handler(calls))
    order = _order(price=Decimal("70000"), quantity=Decimal("10"))  # tick=100 배수(그래도 거부)

    with pytest.raises(OrderValidationError) as exc_info:
        await adapter.place_order(order)

    assert exc_info.value.reason == "UNVERIFIED_SPEC"
    assert calls == []


async def test_place_order_rejects_unverified_symbol_spec_for_market_order() -> None:
    """가격 검사 대상이 아닌 시장가 주문도 lot 판정 이전에 동일하게
    거부돼야 한다 — `verified=False`는 tick뿐 아니라 스냅샷 전체(lot 포함)에
    적용되는 게이트다."""
    calls: list[httpx.Request] = []
    adapter = _make_adapter(_order_cash_handler(calls))
    order = _order(price=None, quantity=Decimal("5"), order_type=OrderType.MARKET)

    with pytest.raises(OrderValidationError) as exc_info:
        await adapter.place_order(order)

    assert exc_info.value.reason == "UNVERIFIED_SPEC"
    assert calls == []
