"""task-7596(BR-21c) -- OKXAccountMixin get_balance/get_positions/get_open_orders/
get_fills/get_order tests.

D2 floor: negative tests >= 3, one failure-injection test, one numeric
performance assertion. DoD: each of the 4 spec'd methods gets >= 1
happy-path + >= 1 negative (empty response/missing field); unsupported
methods fail with `UnsupportedCapabilityError`, never a silent [].
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import pytest

from src.core.exceptions import FatalExchangeError
from src.exchanges.common.adapter import UnsupportedCapabilityError
from src.exchanges.okx.account_mixin import OKXAccountMixin

pytestmark = pytest.mark.asyncio


class _StubClient(OKXAccountMixin):
    def __init__(self, responses: dict[str, dict[str, Any]]) -> None:
        self._responses = responses
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []

    async def _request(
        self, method: str, path: str, *, params: dict[str, Any] | None = None, **_: Any
    ) -> dict[str, Any]:
        self.calls.append((method, path, params))
        return self._responses[path]


# ---- get_balance ----


async def test_get_balance_happy_path_parses_details():
    client = _StubClient(
        {
            "/api/v5/account/balance": {
                "code": "0",
                "data": [
                    {
                        "details": [
                            {
                                "ccy": "USDT",
                                "bal": "1000.5",
                                "availBal": "900.5",
                                "frozenBal": "100",
                            },
                            {"ccy": "BTC", "bal": "0.5", "availBal": "0.5", "frozenBal": "0"},
                        ]
                    }
                ],
            }
        }
    )
    balances = await client.get_balance()
    assert len(balances) == 2
    usdt = next(b for b in balances if b.asset == "USDT")
    assert usdt.total == Decimal("1000.5")
    assert usdt.available == Decimal("900.5")
    assert usdt.used_margin == Decimal("100")


async def test_get_balance_raises_fatal_on_missing_details():
    """Negative 1: an empty data array (venue-side anomaly) must fail
    closed instead of silently returning an empty balance list."""
    client = _StubClient({"/api/v5/account/balance": {"code": "0", "data": []}})
    with pytest.raises(FatalExchangeError):
        await client.get_balance()


async def test_get_balance_raises_fatal_on_row_missing_required_field():
    """Negative 2: a details row missing `bal`/`availBal`."""
    client = _StubClient(
        {"/api/v5/account/balance": {"code": "0", "data": [{"details": [{"ccy": "USDT"}]}]}}
    )
    with pytest.raises(FatalExchangeError):
        await client.get_balance()


# ---- get_positions: explicitly unsupported for this spot-only leaf ----


async def test_get_positions_raises_unsupported_capability_error():
    """DoD 2: unsupported methods raise UnsupportedCapabilityError, never a
    silent empty list (which would misleadingly claim 'zero positions')."""
    client = _StubClient({})
    with pytest.raises(UnsupportedCapabilityError) as exc_info:
        await client.get_positions()
    assert exc_info.value.capability == "get_positions"
    assert client.calls == []  # never reaches the network


# ---- get_open_orders ----


async def test_get_open_orders_happy_path_parses_rows():
    client = _StubClient(
        {
            "/api/v5/trade/orders-pending": {
                "code": "0",
                "data": [
                    {
                        "instId": "BTC-USDT",
                        "ordId": "111",
                        "clOrdId": "c-1",
                        "sz": "1",
                        "side": "buy",
                        "ordType": "limit",
                        "state": "live",
                        "accFillSz": "0",
                        "avgPx": "",
                        "cTime": "1607417337715",
                        "uTime": "1607417337715",
                    }
                ],
            }
        }
    )
    orders = await client.get_open_orders("BTC-USDT")
    assert len(orders) == 1
    assert orders[0].exchange_order_id == "BTC-USDT:111"
    assert orders[0].status.value == "ACKNOWLEDGED"


async def test_get_open_orders_raises_fatal_on_missing_data_key():
    """Negative 3: entirely missing `data` key must fail closed."""
    client = _StubClient({"/api/v5/trade/orders-pending": {"code": "0"}})
    with pytest.raises(FatalExchangeError):
        await client.get_open_orders()


async def test_get_open_orders_raises_fatal_on_row_missing_required_field():
    """Negative 4 / failure-injection: a row missing `sz` must not silently
    produce a zero-quantity order."""
    client = _StubClient(
        {
            "/api/v5/trade/orders-pending": {
                "code": "0",
                "data": [{"instId": "BTC-USDT", "ordId": "111", "side": "buy"}],
            }
        }
    )
    with pytest.raises(FatalExchangeError):
        await client.get_open_orders()


# ---- get_fills ----


async def test_get_fills_happy_path_returns_raw_rows():
    client = _StubClient(
        {
            "/api/v5/trade/fills": {
                "code": "0",
                "data": [
                    {
                        "instId": "BTC-USDT",
                        "tradeId": "t-1",
                        "ordId": "111",
                        "fillPx": "50000",
                        "fillSz": "0.5",
                        "side": "buy",
                        "ts": "1607417337715",
                    }
                ],
            }
        }
    )
    fills = await client.get_fills("BTC-USDT", since=datetime(2020, 1, 1, tzinfo=timezone.utc))
    assert fills == [
        {
            "instId": "BTC-USDT",
            "tradeId": "t-1",
            "ordId": "111",
            "fillPx": "50000",
            "fillSz": "0.5",
            "side": "buy",
            "ts": "1607417337715",
        }
    ]
    _, path, params = client.calls[0]
    assert path == "/api/v5/trade/fills"
    assert params["begin"] == "1577836800000"


async def test_get_fills_raises_fatal_on_missing_data_key():
    """Negative 5: entirely missing `data` key."""
    client = _StubClient({"/api/v5/trade/fills": {"code": "0"}})
    with pytest.raises(FatalExchangeError):
        await client.get_fills()


async def test_get_fills_rejects_naive_datetime_since():
    """Negative 6: a naive `since` datetime is rejected fail-closed instead
    of guessing a timezone (would silently misalign the fills window)."""
    client = _StubClient({})
    with pytest.raises(ValueError):
        await client.get_fills(since=datetime(2020, 1, 1))  # noqa: DTZ001 -- deliberately naive
    assert client.calls == []


# ---- get_order ----


async def test_get_order_happy_path_parses_composite_id():
    client = _StubClient(
        {
            "/api/v5/trade/order": {
                "code": "0",
                "data": [
                    {
                        "instId": "BTC-USDT",
                        "ordId": "111",
                        "clOrdId": "c-1",
                        "sz": "1",
                        "side": "sell",
                        "ordType": "market",
                        "state": "filled",
                        "accFillSz": "1",
                        "avgPx": "50000",
                        "cTime": "1607417337715",
                        "uTime": "1607417337715",
                    }
                ],
            }
        }
    )
    order = await client.get_order("BTC-USDT:111")
    assert order.status.value == "FILLED"
    assert order.average_fill_price is not None
    assert order.average_fill_price.amount == Decimal("50000")


async def test_get_order_raises_fatal_on_malformed_exchange_order_id():
    """Negative 7: a composite id without the ':' separator is rejected
    before any network call."""
    client = _StubClient({})
    with pytest.raises(FatalExchangeError):
        await client.get_order("not-a-composite-id")
    assert client.calls == []


async def test_get_order_raises_fatal_when_not_found():
    client = _StubClient({"/api/v5/trade/order": {"code": "0", "data": []}})
    with pytest.raises(FatalExchangeError):
        await client.get_order("BTC-USDT:999")


# ---- numeric performance assertion ----


@pytest.mark.perf
async def test_get_balance_latency_budget():
    """Numeric performance assertion: pure stub-client parsing overhead
    must average under 1ms across 100 calls (no network)."""
    client = _StubClient(
        {
            "/api/v5/account/balance": {
                "code": "0",
                "data": [{"details": [{"ccy": "USDT", "bal": "1", "availBal": "1"}]}],
            }
        }
    )
    start = time.perf_counter()
    for _ in range(100):
        await client.get_balance()
    elapsed = time.perf_counter() - start
    assert elapsed / 100 < 0.001
