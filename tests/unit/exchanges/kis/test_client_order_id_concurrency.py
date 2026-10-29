"""task-8079(AUDIT F6/§1) — KIS concurrency: concurrent retries with the same
client_order_id must not double-submit via asyncio.Lock in place_order().

Spec: docs/audits/AUDIT_2026-09-26_order_path.md F6/§1.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Generator
from decimal import Decimal

import httpx
import pytest

from src.data.models.base import AssetClass, Currency, Money
from src.data.models.trading import Order, OrderSide, OrderType
from src.exchanges.kis import rate_profile
from src.exchanges.kis.adapter import KISAdapter
from src.services.oms.domain.symbol_registry import SymbolRegistry

_TOKEN_PATH = "/oauth2/tokenP"
_TOKEN_RESPONSE = {"access_token": "t", "access_token_token_expired": "2099-01-01 00:00:00"}
_ORDER_CASH_PATH = "/uapi/domestic-stock/v1/trading/order-cash"
_DAILY_CCLD_PATH = "/uapi/domestic-stock/v1/trading/inquire-daily-ccld"


def _verified_registry() -> SymbolRegistry:
    """task-8337 -- the production 005930.KS snapshot is `verified=False`
    (see test_trading_mixin_precheck.py); this file's concern is
    client_order_id correlation, not tick/lot verification-gating, so it
    needs an explicit `verified=True` test double to reach `place_order()`'s
    HTTP call at all."""
    registry = SymbolRegistry()
    registry.register(
        "005930.KS",
        "kis",
        "005930",
        tick=Decimal("100"),
        lot=Decimal("1"),
        min_notional=Decimal("0"),
        quote_ccy="KRW",
        verified=True,
    )
    return registry


@pytest.fixture(autouse=True)
def _reset_bucket_registry() -> Generator[None]:
    """BR-2b — process-wide TokenBucket 싱글턴을 테스트 간 격리한다
    (test_trading_mixin_precheck.py와 동일 이유)."""
    rate_profile.reset_token_bucket_registry_for_test()
    yield
    rate_profile.reset_token_bucket_registry_for_test()


def _make_adapter(
    handler: Callable[[httpx.Request], httpx.Response]
    | Callable[[httpx.Request], Awaitable[httpx.Response]],
) -> KISAdapter:
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(
        base_url="https://openapivts.koreainvestment.com:29443", transport=transport
    )
    return KISAdapter(
        "app", "secret", "12345678", "01", is_paper_trading=True, http_client=http_client
    )


def _order(*, client_order_id: str = "c-1", symbol: str = "005930") -> Order:
    return Order(
        client_order_id=client_order_id,
        strategy_id="s-1",
        strategy_version="v1",
        symbol=symbol,
        exchange="kis",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=Decimal("10"),
        price=Money(amount=Decimal("70000"), currency=Currency.KRW),
        asset_class=AssetClass.KR_EQUITY,
    )


def _handler(
    order_calls: list[httpx.Request],
    ccld_calls: list[httpx.Request],
    *,
    orgno: str = "01234",
    odno: str = "0000001",
) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == _TOKEN_PATH:
            return httpx.Response(200, json=_TOKEN_RESPONSE)
        if request.url.path == _ORDER_CASH_PATH:
            order_calls.append(request)
            return httpx.Response(
                200,
                json={
                    "rt_cd": "0",
                    "msg1": "ok",
                    "output": {"KRX_FWDG_ORD_ORGNO": orgno, "ODNO": odno},
                },
            )
        assert request.url.path == _DAILY_CCLD_PATH
        ccld_calls.append(request)
        return httpx.Response(
            200,
            json={
                "rt_cd": "0",
                "output1": [
                    {
                        "pdno": "005930",
                        "sll_buy_dvsn_cd": "02",
                        "ord_qty": "10",
                        "tot_ccld_qty": "0",
                    }
                ],
            },
        )

    return handler


def _blocking_handler(
    order_calls: list[httpx.Request],
    ccld_calls: list[httpx.Request],
    *,
    entered: asyncio.Event,
    release: asyncio.Event,
    orgno: str = "01234",
    odno: str = "0000001",
) -> Callable[[httpx.Request], Awaitable[httpx.Response]]:
    """order-cash 응답을 `release`가 set될 때까지 보류해, 두 place_order()
    호출이 실제로 동시에 in-flight인 구간을 만든다."""

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == _TOKEN_PATH:
            return httpx.Response(200, json=_TOKEN_RESPONSE)
        if request.url.path == _ORDER_CASH_PATH:
            order_calls.append(request)
            entered.set()
            await release.wait()
            return httpx.Response(
                200,
                json={
                    "rt_cd": "0",
                    "msg1": "ok",
                    "output": {"KRX_FWDG_ORD_ORGNO": orgno, "ODNO": odno},
                },
            )
        assert request.url.path == _DAILY_CCLD_PATH
        ccld_calls.append(request)
        return httpx.Response(
            200,
            json={
                "rt_cd": "0",
                "output1": [
                    {
                        "pdno": "005930",
                        "sll_buy_dvsn_cd": "02",
                        "ord_qty": "10",
                        "tot_ccld_qty": "0",
                    }
                ],
            },
        )

    return handler


# ---------------------------------------------------------------------------
# concurrent — 같은 client_order_id로 동시 재시도하면 락이 이중 제출을 막는다
# ---------------------------------------------------------------------------


async def test_concurrent_retries_with_same_client_order_id_do_not_double_submit() -> None:
    order_calls: list[httpx.Request] = []
    ccld_calls: list[httpx.Request] = []
    entered = asyncio.Event()
    release = asyncio.Event()
    adapter = _make_adapter(
        _blocking_handler(order_calls, ccld_calls, entered=entered, release=release)
    )
    adapter.symbol_registry = _verified_registry
    order = _order(client_order_id="concurrent-1")

    task1 = asyncio.create_task(adapter.place_order(order))
    await entered.wait()  # task1이 지금 order-cash 안에서 블록돼 있다
    task2 = asyncio.create_task(adapter.place_order(order))
    # task2는 place_order 내부의 asyncio.Lock에서 대기한다.
    # entered.wait() 시점에 task1이 이미 lock을 획득했으므로 task2는
    # order-cash 핸들러에 도달하지 못하고 락 대기 상태로 블록된다.
    release.set()

    first, second = await asyncio.gather(task1, task2)

    assert len(order_calls) == 1  # 락이 두 번째 진입의 재제출을 막았다
    assert first.exchange_order_id == "01234:0000001"
    assert second.exchange_order_id == first.exchange_order_id
