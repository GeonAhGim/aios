"""02b_bitget_api_v2_full_spec_v1.md Section 4 integration tests — Margin P2.

Task-6804 (BR-16, ADR-2026-09-06-I Decision 5, ADR-2026-09-24-A Decision 5):
the 5 remaining margin P2 endpoints (flash-repay, max-transfer-out-amount,
batch-place-order, tier-data, and the borrow/repay/interest/liquidation
history quartet). Split from test_bitget_margin.py to stay under the
500-line file-policy warn threshold (ADR-2026-09-10-C Section 7).
Same httpx.MockTransport pattern as test_bitget_margin.py — no real Bitget
Demo account available.
"""
import json
from decimal import Decimal

import httpx
import pytest

from src.core.exceptions import FrozenZonePaperAdapterBlockedError
from src.data.models.base import AssetClass
from src.data.models.trading import Order, OrderSide, OrderStatus, OrderType
from src.exchanges.bitget.adapter import BitgetAdapter
from src.exchanges.bitget.margin_mixin import CROSSED, ISOLATED


def _make_adapter(handler) -> BitgetAdapter:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(base_url="https://api.bitget.com", transport=transport)
    return BitgetAdapter("key", "secret", "passphrase", demo_mode=True, http_client=client)


def _json_response(payload: dict, status_code: int = 200) -> httpx.Response:
    return httpx.Response(status_code, json=payload)


async def test_get_max_transfer_out_amount():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/margin/crossed/account/max-transfer-out-amount"
        assert request.url.params["coin"] == "USDT"
        return _json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": {"maxTransferOutAmount": "250.5"},
            }
        )

    adapter = _make_adapter(handler)
    amount = await adapter.get_max_transfer_out_amount(CROSSED, "usdt")

    assert amount == Decimal("250.5")


async def test_get_max_transfer_out_amount_rejects_invalid_margin_type():
    """Negative: margin_type validation fires before the request is built."""
    adapter = _make_adapter(lambda request: _json_response({"code": "00000", "data": {}}))
    with pytest.raises(ValueError):
        await adapter.get_max_transfer_out_amount("bogus", "usdt")


async def test_get_margin_tier_data_returns_raw_rows():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/margin/isolated/tier-data"
        assert request.url.params["coin"] == "USDT"
        return _json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [{"tier": "1", "leverage": "10"}],
            }
        )

    adapter = _make_adapter(handler)
    tiers = await adapter.get_margin_tier_data(ISOLATED, "usdt", symbol="BTC/USDT")

    assert tiers == [{"tier": "1", "leverage": "10"}]


async def test_flash_repay_margin_sends_coin_list():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/margin/crossed/account/flash-repay"
        body = json.loads(request.content)
        assert body == {"coinList": ["USDT"]}
        return _json_response(
            {"code": "00000", "msg": "success", "requestTime": 1, "data": {"coinList": ["USDT"]}}
        )

    adapter = _make_adapter(handler)
    result = await adapter.flash_repay_margin(CROSSED, ["usdt"])

    assert result == {"coinList": ["USDT"]}


async def test_flash_repay_margin_rejects_empty_coin_list():
    """Negative: an empty coin_list must not send a request."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("A request escaped validation.")

    adapter = _make_adapter(handler)
    with pytest.raises(ValueError):
        await adapter.flash_repay_margin(CROSSED, [])


async def test_flash_repay_margin_blocked_on_live_configured_adapter():
    """Red-team #2026-09-02-32 principle — write endpoints are blocked on LIVE adapters."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("The guard should have blocked this request.")

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(base_url="https://api.bitget.com", transport=transport)
    live_adapter = BitgetAdapter(
        "key", "secret", "passphrase", demo_mode=False, http_client=client
    )

    with pytest.raises(FrozenZonePaperAdapterBlockedError):
        await live_adapter.flash_repay_margin(CROSSED, ["usdt"])


async def test_batch_place_margin_orders():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/margin/crossed/batch-place-order"
        body = json.loads(request.content)
        assert len(body["orderList"]) == 2
        return _json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": {
                    "resultList": [
                        {"orderId": "1", "clientOid": "c-1"},
                        {"orderId": "2", "clientOid": "c-2"},
                    ]
                },
            }
        )

    adapter = _make_adapter(handler)
    orders = [
        Order(
            client_order_id=f"c-{i}",
            strategy_id="s-1",
            strategy_version="v1",
            symbol="BTC/USDT",
            exchange="bitget",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=Decimal("0.01"),
            asset_class=AssetClass.CRYPTO,
        )
        for i in (1, 2)
    ]

    results = await adapter.batch_place_margin_orders(CROSSED, orders)

    assert [r.exchange_order_id for r in results] == ["1", "2"]
    assert all(r.status == OrderStatus.SUBMITTED for r in results)


async def test_batch_place_margin_orders_rejects_empty_list():
    adapter = _make_adapter(lambda request: _json_response({"code": "00000", "data": {}}))
    with pytest.raises(ValueError):
        await adapter.batch_place_margin_orders(CROSSED, [])


async def test_batch_place_margin_orders_rejects_non_positive_quantity():
    """Red-team #2026-09-02-33-style regression: one non-positive-quantity
    order in the batch must reject the whole batch."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("A request escaped validation.")

    adapter = _make_adapter(handler)
    bad_order = Order(
        client_order_id="c-1",
        strategy_id="s-1",
        strategy_version="v1",
        symbol="BTC/USDT",
        exchange="bitget",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("0"),
        asset_class=AssetClass.CRYPTO,
    )
    with pytest.raises(ValueError):
        await adapter.batch_place_margin_orders(CROSSED, [bad_order])


async def test_get_margin_history_borrow():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/margin/crossed/borrow-history"
        return _json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [{"loanId": "1", "coin": "USDT", "borrowAmount": "100"}],
            }
        )

    adapter = _make_adapter(handler)
    rows = await adapter.get_margin_history(CROSSED, "borrow", coin="usdt")

    assert rows == [{"loanId": "1", "coin": "USDT", "borrowAmount": "100"}]


async def test_get_margin_history_liquidation():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/margin/isolated/liquidation-history"
        return _json_response(
            {"code": "00000", "msg": "success", "requestTime": 1, "data": []}
        )

    adapter = _make_adapter(handler)
    rows = await adapter.get_margin_history(ISOLATED, "liquidation", symbol="BTC/USDT")

    assert rows == []


async def test_get_margin_history_rejects_unknown_history_type():
    """Negative: a history_type outside the whitelist is rejected before
    the request is built."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("A request escaped validation.")

    adapter = _make_adapter(handler)
    with pytest.raises(ValueError):
        await adapter.get_margin_history(CROSSED, "withdrawal")
