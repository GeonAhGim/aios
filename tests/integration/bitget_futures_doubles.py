"""Shared helpers for the Bitget futures integration tests
(`test_bitget_futures_market.py`, `test_bitget_futures_account_position.py`,
`test_bitget_futures_order.py`).

02b_bitget_api_v2_full_spec_v1.md §5 통합테스트 — Futures/Mix P0.

실제 Bitget Demo 계정 API 키가 없는 상태라 httpx.MockTransport로 응답
형태를 재현해 검증한다(test_bitget_adapter.py와 동일 원칙) — 필드명은
커뮤니티 SDK 레퍼런스 기준 최선 추정치라 라이브 검증 전까지는 확정 아님.
"""
from decimal import Decimal

import httpx

from src.data.models.base import AssetClass
from src.data.models.trading import Order, OrderSide, OrderType
from src.exchanges.bitget.adapter import BitgetAdapter


def make_adapter(handler) -> BitgetAdapter:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(base_url="https://api.bitget.com", transport=transport)
    return BitgetAdapter("key", "secret", "passphrase", demo_mode=True, http_client=client)


def json_response(payload: dict, status_code: int = 200) -> httpx.Response:
    return httpx.Response(status_code, json=payload)


def make_order() -> Order:
    return Order(
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
