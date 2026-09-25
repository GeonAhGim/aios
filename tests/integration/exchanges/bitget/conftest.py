"""Shared fixtures for the Bitget adapter mock-transport test split (task-4225).

Split out of the former single `tests/integration/test_bitget_adapter.py`
(788 lines) into `test_bitget_market_data.py`, `test_bitget_orders.py`, and
`test_bitget_retry.py`, each under the 500-line warn threshold.
"""
from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from typing import Any

import httpx
import pytest

from src.data.models.base import AssetClass
from src.data.models.trading import Order, OrderSide, OrderType
from src.exchanges.bitget.adapter import BitgetAdapter

REAL_TICKER_ENVELOPE = {
    "code": "00000",
    "msg": "success",
    "requestTime": 1787851010117,
    "data": [
        {
            "open": "78217.08",
            "symbol": "BTCUSDT",
            "high24h": "80800",
            "low24h": "78196",
            "lastPr": "80663.08",
            "quoteVolume": "270635812.383435",
            "baseVolume": "3407.420693",
            "usdtVolume": "270635812.38343424",
            "ts": "1787851009318",
            "bidPr": "80664.02",
            "askPr": "80664.03",
            "bidSz": "0.859943",
            "askSz": "0.29158",
            "openUtc": "79023.47",
            "changeUtc24h": "0.02075",
            "change24h": "0.03127",
        }
    ],
}


@pytest.fixture
def real_ticker_envelope() -> dict:
    return REAL_TICKER_ENVELOPE


@pytest.fixture
def make_adapter() -> Callable[..., BitgetAdapter]:
    def _make_adapter(handler, *, sleep_fn=None) -> BitgetAdapter:
        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(base_url="https://api.bitget.com", transport=transport)
        return BitgetAdapter(
            "key", "secret", "passphrase", demo_mode=True, http_client=client, sleep_fn=sleep_fn
        )

    return _make_adapter


@pytest.fixture
def json_response() -> Callable[..., httpx.Response]:
    def _json_response(payload: dict, status_code: int = 200) -> httpx.Response:
        return httpx.Response(status_code, json=payload)

    return _json_response


@pytest.fixture
def order_row() -> Callable[..., dict]:
    def _order_row(**overrides: object) -> dict:
        row = {
            "orderId": "999",
            "clientOid": "c-1",
            "symbol": "BTCUSDT",
            "side": "buy",
            "orderType": "limit",
            "size": "0.01",
            "status": "live",
        }
        row.update(overrides)
        return row

    return _order_row


@pytest.fixture
def new_order() -> Callable[..., Order]:
    def _new_order(**overrides: Any) -> Order:
        defaults: dict[str, Any] = {
            "client_order_id": "c-1",
            "strategy_id": "s-1",
            "strategy_version": "v1",
            "symbol": "BTC/USDT",
            "exchange": "bitget",
            "side": OrderSide.BUY,
            "order_type": OrderType.LIMIT,
            "quantity": Decimal("0.01"),
            "asset_class": AssetClass.CRYPTO,
            "price": None,
        }
        defaults.update(overrides)
        return Order(**defaults)

    return _new_order
