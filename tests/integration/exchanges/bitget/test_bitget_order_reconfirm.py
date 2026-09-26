"""task-8081 (F8 audit fix) — ambiguous 5xx/timeout self-reconfirm.

Split out of `test_bitget_orders.py` (loc_over_500 ratchet, task-8081) —
covers `place_order`/`cancel_order` self-reconfirming via
`find_order_by_client_id`/`get_order` when a 5xx/timeout exhausts every
retry attempt (AUDIT_2026-09-26_order_path.md F8).
"""

from decimal import Decimal

import httpx
import pytest

from src.core.exceptions import RetryableExchangeError
from src.data.models.base import AssetClass
from src.data.models.trading import Order, OrderSide, OrderStatus, OrderType


async def test_place_order_reconfirms_via_find_order_by_client_id_after_5xx_exhausted(
    make_adapter, json_response, order_row
):
    """DoD 1/2 — a place-order 5xx that exhausts every retry attempt is
    ambiguous (the exchange may have already accepted it). place_order must
    reconfirm via find_order_by_client_id and, once found, must NOT resend
    place-order (no duplicate submission)."""
    place_order_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal place_order_calls
        if request.url.path == "/api/v2/spot/trade/place-order":
            place_order_calls += 1
            return httpx.Response(503, text="service unavailable")
        assert request.url.path == "/api/v2/spot/trade/orderInfo"
        assert request.url.params["clientOid"] == "c-1"
        return json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [order_row(status="live")],
            }
        )

    async def fake_sleep(seconds: float) -> None:
        return None

    adapter = make_adapter(handler, sleep_fn=fake_sleep)
    order = Order(
        client_order_id="c-1",
        strategy_id="s-1",
        strategy_version="v1",
        symbol="BTC/USDT",
        exchange="bitget",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("0.01"),
        asset_class=AssetClass.CRYPTO,
    )

    result = await adapter.place_order(order)

    assert place_order_calls == 4  # ResilientTransport max_attempts, no extra resend
    assert result.exchange_order_id == "999"
    assert result.status == OrderStatus.ACKNOWLEDGED
    assert result.client_order_id == "c-1"  # caller-supplied domain fields preserved


async def test_place_order_reraises_when_reconfirm_also_finds_nothing(make_adapter, json_response):
    """Negative — if the exchange doesn't know the client_order_id either,
    the order is genuinely UNKNOWN. place_order must not swallow the error;
    the external unknown_resolver (L4-16) is the only thing allowed to keep
    retrying that case."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/spot/trade/place-order":
            return httpx.Response(503, text="service unavailable")
        assert request.url.path == "/api/v2/spot/trade/orderInfo"
        return json_response({"code": "00000", "msg": "success", "requestTime": 1, "data": []})

    async def fake_sleep(seconds: float) -> None:
        return None

    adapter = make_adapter(handler, sleep_fn=fake_sleep)
    order = Order(
        client_order_id="c-2",
        strategy_id="s-1",
        strategy_version="v1",
        symbol="BTC/USDT",
        exchange="bitget",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("0.01"),
        asset_class=AssetClass.CRYPTO,
    )

    with pytest.raises(RetryableExchangeError):
        await adapter.place_order(order)


async def test_cancel_order_reconfirms_via_get_order_after_5xx_exhausted(
    make_adapter, json_response, order_row
):
    """cancel_order only has the exchange order_id (not client_order_id), so
    its self-reconfirm path uses get_order(order_id) instead of
    find_order_by_client_id — same ambiguous-response intent as place_order."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/spot/trade/cancel-order":
            return httpx.Response(503, text="service unavailable")
        assert request.url.path == "/api/v2/spot/trade/orderInfo"
        assert request.url.params["orderId"] == "999"
        return json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": order_row(status="cancelled"),
            }
        )

    async def fake_sleep(seconds: float) -> None:
        return None

    adapter = make_adapter(handler, sleep_fn=fake_sleep)
    assert await adapter.cancel_order("999") is True


async def test_cancel_order_reraises_when_reconfirm_shows_order_still_open(
    make_adapter, json_response, order_row
):
    """Negative — if reconfirmation shows the order is still open (the
    cancel never actually landed), cancel_order must not silently report
    success; the original transport error propagates."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/spot/trade/cancel-order":
            return httpx.Response(503, text="service unavailable")
        return json_response(
            {"code": "00000", "msg": "success", "requestTime": 1, "data": order_row(status="live")}
        )

    async def fake_sleep(seconds: float) -> None:
        return None

    adapter = make_adapter(handler, sleep_fn=fake_sleep)
    with pytest.raises(RetryableExchangeError):
        await adapter.cancel_order("999")
