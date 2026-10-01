"""Shared helpers for the Bitget futures integration tests
(`test_bitget_futures_market.py`, `test_bitget_futures_account_position.py`,
`test_bitget_futures_order.py`).

02b_bitget_api_v2_full_spec_v1.md §5 통합테스트 — Futures/Mix P0.

실제 Bitget Demo 계정 API 키가 없는 상태라 httpx.MockTransport로 응답
형태를 재현해 검증한다(test_bitget_adapter.py와 동일 원칙) — 필드명은
커뮤니티 SDK 레퍼런스 기준 최선 추정치라 라이브 검증 전까지는 확정 아님.

task-10248 DEEPEN — 이 파일 자체가 `make_order`/`make_adapter` 불변식을
지키는지 검증하는 negative/실패주입 케이스를 담는다(다른 3개 테스트
파일은 이미 성공 경로를 다룬다).
"""

from collections.abc import Awaitable, Callable
from decimal import Decimal
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from src.core.exceptions import RetryableExchangeError
from src.data.models.base import AssetClass
from src.data.models.trading import Order, OrderSide, OrderType
from src.exchanges.bitget.adapter import BitgetAdapter


def make_adapter(
    handler, *, sleep_fn: Callable[[float], Awaitable[None]] | None = None
) -> BitgetAdapter:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(base_url="https://api.bitget.com", transport=transport)
    return BitgetAdapter(
        "key", "secret", "passphrase", demo_mode=True, http_client=client, sleep_fn=sleep_fn
    )


def json_response(payload: dict, status_code: int = 200) -> httpx.Response:
    return httpx.Response(status_code, json=payload)


def make_order(**overrides: Any) -> Order:
    fields: dict[str, Any] = {
        "client_order_id": "c-1",
        "strategy_id": "s-1",
        "strategy_version": "v1",
        "symbol": "BTC/USDT",
        "exchange": "bitget",
        "side": OrderSide.BUY,
        "order_type": OrderType.MARKET,
        "quantity": Decimal("0.01"),
        "asset_class": AssetClass.CRYPTO,
    }
    fields.update(overrides)
    return Order(**fields)


async def _instant_sleep(_seconds: float) -> None:
    """실패주입 테스트용 — 재시도 백오프(최대 attempt=4, cap 30s)를
    기다리지 않고 바로 진행해 ResilientTransport의 재시도 소진 경로만
    검증한다."""


def test_make_order_rejects_non_decimal_quantity() -> None:
    with pytest.raises(ValidationError):
        make_order(quantity="not-a-number")


def test_make_order_rejects_missing_asset_class() -> None:
    fields = {
        "client_order_id": "c-1",
        "strategy_id": "s-1",
        "strategy_version": "v1",
        "symbol": "BTC/USDT",
        "exchange": "bitget",
        "side": OrderSide.BUY,
        "order_type": OrderType.MARKET,
        "quantity": Decimal("0.01"),
    }
    with pytest.raises(ValidationError):
        Order(**fields)


def test_make_order_rejects_invalid_side() -> None:
    with pytest.raises(ValidationError):
        make_order(side="HOLD")


def test_make_order_rejects_invalid_order_type() -> None:
    with pytest.raises(ValidationError):
        make_order(order_type="ICEBERG")


async def test_adapter_retries_exhausted_raises_retryable_on_persistent_connect_error() -> None:
    """실패주입 — handler가 매 시도마다 httpx.ConnectError를 던져 네트워크
    장애를 재현한다. ResilientTransport가 max_attempts(4)까지 재시도한 뒤
    TRANSIENT_NETWORK(retryable=True)로 분류해 RetryableExchangeError를
    올려야 한다(src/exchanges/common/transport.py _request_with_retry)."""

    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ConnectError("boom")

    adapter = make_adapter(handler, sleep_fn=_instant_sleep)

    with pytest.raises(RetryableExchangeError):
        await adapter.get_futures_order("777", symbol="BTC/USDT")

    assert attempts == 4
