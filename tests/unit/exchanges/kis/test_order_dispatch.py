"""task-8148 -- order_dispatch.py's domestic-cash branch calls
`KISTradingMixin.place_order(adapter, order)` unbound, where `place_order`
is typed `self: _OrderSubmittingClient` (task-8074, needs `venue_profile()`
for tick/lot/min_notional pre-validation). The call site previously cast
`adapter` to `KISHTTPClient` -- a type lacking `venue_profile()` -- so
mypy correctly flagged it red; `_DispatchableAdapter` now inherits
`_OrderSubmittingClient` directly instead of using `cast`/`type: ignore`.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md #10 BR (order_dispatch).

This exercises `dispatch_place_order` directly (not `KISAdapter.place_order`,
already covered by test_trading_mixin_precheck.py) to prove the fix keeps
task-8074's tick/lot pre-validation reachable through the dispatch branch
itself, and that a real `KISAdapter` instance satisfies `_DispatchableAdapter`
at both type-check and run time without any cast.
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
from src.exchanges.kis.order_dispatch import dispatch_place_order
from src.services.oms.domain.errors import OrderValidationError

_TOKEN_PATH = "/oauth2/tokenP"
_TOKEN_RESPONSE = {"access_token": "t", "access_token_token_expired": "2099-01-01 00:00:00"}


@pytest.fixture(autouse=True)
def _reset_bucket_registry() -> Generator[None, None, None]:
    """BR-2b -- process-wide TokenBucket singleton isolation between tests
    (same reason as test_trading_mixin_precheck.py)."""
    rate_profile.reset_token_bucket_registry_for_test()
    yield
    rate_profile.reset_token_bucket_registry_for_test()


def _make_adapter(handler: Callable[[httpx.Request], httpx.Response]) -> KISAdapter:
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(
        base_url="https://openapivts.koreainvestment.com:29443", transport=transport
    )
    return KISAdapter(
        "app", "secret", "12345678", "01", is_paper_trading=True, http_client=http_client
    )


def _order(*, price: Decimal | None, quantity: Decimal) -> Order:
    return Order(
        client_order_id="c-1",
        strategy_id="s-1",
        strategy_version="v1",
        symbol="005930",
        exchange="kis",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
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
# negative (>=3) -- dispatch_place_order's domestic-cash branch must still
# reach task-8074's tick/lot/min_notional pre-validation, not bypass it.
# ---------------------------------------------------------------------------


async def test_dispatch_rejects_tick_misaligned_price_without_calling_exchange() -> None:
    calls: list[httpx.Request] = []
    adapter = _make_adapter(_order_cash_handler(calls))
    order = _order(price=Decimal("70050"), quantity=Decimal("10"))  # tick=100 배수 아님

    with pytest.raises(OrderValidationError) as exc_info:
        await dispatch_place_order(adapter, order)

    assert exc_info.value.reason == "TICK_MISALIGNED"
    assert calls == []


async def test_dispatch_rejects_lot_misaligned_quantity_without_calling_exchange() -> None:
    calls: list[httpx.Request] = []
    adapter = _make_adapter(_order_cash_handler(calls))
    order = _order(price=Decimal("70000"), quantity=Decimal("1.5"))  # lot=1 배수 아님

    with pytest.raises(OrderValidationError) as exc_info:
        await dispatch_place_order(adapter, order)

    assert exc_info.value.reason == "LOT_MISALIGNED"
    assert calls == []


async def test_dispatch_rejects_below_min_notional_without_calling_exchange() -> None:
    calls: list[httpx.Request] = []
    adapter = _make_adapter(_order_cash_handler(calls))
    original_profile = adapter.venue_profile()
    patched_profile = original_profile.model_copy(
        update={"min_notional": {"005930.KS": Decimal("1000000")}}
    )
    adapter.venue_profile = lambda: patched_profile
    order = _order(price=Decimal("70000"), quantity=Decimal("1"))  # notional=70000 < 1,000,000

    with pytest.raises(OrderValidationError) as exc_info:
        await dispatch_place_order(adapter, order)

    assert exc_info.value.reason == "MIN_NOTIONAL"
    assert calls == []


# ---------------------------------------------------------------------------
# failure-injection -- pre-validation errors propagate out of
# dispatch_place_order rather than being swallowed into a fake SUBMITTED order.
# ---------------------------------------------------------------------------


async def test_dispatch_precheck_failure_does_not_return_a_submitted_order() -> None:
    calls: list[httpx.Request] = []
    adapter = _make_adapter(_order_cash_handler(calls))
    order = _order(price=Decimal("70050"), quantity=Decimal("10"))

    try:
        result = await dispatch_place_order(adapter, order)
    except OrderValidationError:
        result = None

    assert result is None
    assert calls == []


# ---------------------------------------------------------------------------
# no regression -- an aligned order still reaches the exchange through the
# dispatch branch (proves the fix did not accidentally short-circuit routing).
# ---------------------------------------------------------------------------


async def test_dispatch_accepts_aligned_order_and_calls_exchange() -> None:
    calls: list[httpx.Request] = []
    adapter = _make_adapter(_order_cash_handler(calls))
    order = _order(price=Decimal("70000"), quantity=Decimal("10"))

    result = await dispatch_place_order(adapter, order)

    assert len(calls) == 1
    assert result.status == OrderStatus.SUBMITTED
    assert result.exchange_order_id == "01234:0000001"
