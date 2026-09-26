"""task-8079(AUDIT F6/§1) — KIS has no client_order_id concept, so the OMS's
client_order_id-based idempotent retry/duplicate-response adoption is
structurally inert on KIS (task-7978 finding). `KISTradingMixin` now maps
`client_order_id -> KIS 'orgno:odno'` per adapter instance so a retried
request resolves to the previously-assigned ODNO instead of resubmitting
(correlation recovery, not a new idempotency guarantee — KIS itself still
has no client_order_id field).

Spec: docs/audits/AUDIT_2026-09-26_order_path.md F6/§1.
"""

from __future__ import annotations

from collections.abc import Callable, Generator
from decimal import Decimal

import httpx
import pytest

from src.data.models.base import AssetClass, Currency, Money
from src.data.models.trading import Order, OrderSide, OrderType
from src.exchanges.kis import rate_profile
from src.exchanges.kis.adapter import KISAdapter
from src.exchanges.kis.trading_mixin import ClientOrderIdNotMappedError

_TOKEN_PATH = "/oauth2/tokenP"
_TOKEN_RESPONSE = {"access_token": "t", "access_token_token_expired": "2099-01-01 00:00:00"}
_ORDER_CASH_PATH = "/uapi/domestic-stock/v1/trading/order-cash"
_DAILY_CCLD_PATH = "/uapi/domestic-stock/v1/trading/inquire-daily-ccld"


@pytest.fixture(autouse=True)
def _reset_bucket_registry() -> Generator[None, None, None]:
    """BR-2b — process-wide TokenBucket 싱글턴을 테스트 간 격리한다
    (test_trading_mixin_precheck.py와 동일 이유)."""
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


# ---------------------------------------------------------------------------
# positive — 재시도는 기존 ODNO 매핑을 반환하고 새 주문을 내지 않는다 (DoD 1)
# ---------------------------------------------------------------------------


async def test_place_order_retry_returns_existing_odno_without_resubmitting() -> None:
    order_calls: list[httpx.Request] = []
    ccld_calls: list[httpx.Request] = []
    adapter = _make_adapter(_handler(order_calls, ccld_calls))
    order = _order(client_order_id="retry-1")

    first = await adapter.place_order(order)
    second = await adapter.place_order(order)

    assert len(order_calls) == 1  # 두 번째 호출은 order-cash로 나가지 않았다
    assert len(ccld_calls) == 1  # 대신 기존 ODNO를 재조회했다
    assert first.exchange_order_id == "01234:0000001"
    assert second.exchange_order_id == first.exchange_order_id
    assert second.client_order_id == "retry-1"


# ---------------------------------------------------------------------------
# negative (>=3) — 무음 실패 금지: 매핑이 없으면 명시적으로 실패한다
# ---------------------------------------------------------------------------


async def test_resolve_client_order_id_raises_when_never_submitted() -> None:
    order_calls: list[httpx.Request] = []
    ccld_calls: list[httpx.Request] = []
    adapter = _make_adapter(_handler(order_calls, ccld_calls))

    with pytest.raises(ClientOrderIdNotMappedError) as exc_info:
        adapter.resolve_client_order_id("never-seen")

    assert exc_info.value.client_order_id == "never-seen"


async def test_resolve_client_order_id_raises_for_different_id_after_a_real_submission() -> None:
    """한 client_order_id를 제출했다고 해서 다른(유사한) id까지 매핑되지
    않는다 — 매핑은 정확히 일치해야 한다."""
    order_calls: list[httpx.Request] = []
    ccld_calls: list[httpx.Request] = []
    adapter = _make_adapter(_handler(order_calls, ccld_calls))
    await adapter.place_order(_order(client_order_id="c-1"))

    with pytest.raises(ClientOrderIdNotMappedError) as exc_info:
        adapter.resolve_client_order_id("c-2")

    assert exc_info.value.client_order_id == "c-2"


async def test_place_order_with_empty_client_order_id_never_dedupes() -> None:
    """client_order_id=""(빈 문자열)는 애초에 상관관계 키가 될 수 없으므로
    재시도 감지 대상에서 제외돼야 한다 — 매번 새 주문으로 나가고, 매핑도
    남기지 않아 resolve_client_order_id("")도 실패해야 한다."""
    order_calls: list[httpx.Request] = []
    ccld_calls: list[httpx.Request] = []
    adapter = _make_adapter(_handler(order_calls, ccld_calls))
    order = _order(client_order_id="")

    await adapter.place_order(order)
    await adapter.place_order(order)

    assert len(order_calls) == 2  # 매번 실제로 거래소에 나갔다 (dedup 안 됨)
    with pytest.raises(ClientOrderIdNotMappedError):
        adapter.resolve_client_order_id("")


# ---------------------------------------------------------------------------
# 회귀 없음 — 서로 다른 client_order_id는 각각 독립적으로 매핑된다
# ---------------------------------------------------------------------------


async def test_different_client_order_ids_map_to_distinct_entries_both_submitted() -> None:
    order_calls: list[httpx.Request] = []
    ccld_calls: list[httpx.Request] = []
    adapter = _make_adapter(_handler(order_calls, ccld_calls, orgno="01234", odno="0000001"))

    await adapter.place_order(_order(client_order_id="c-1"))
    await adapter.place_order(_order(client_order_id="c-2"))

    assert len(order_calls) == 2  # 서로 다른 id는 재시도로 취급되지 않는다
    assert adapter.resolve_client_order_id("c-1") == "01234:0000001"
    assert adapter.resolve_client_order_id("c-2") == "01234:0000001"
