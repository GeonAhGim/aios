"""L4-31(task-2514) — BitgetAdapter 전체를 통해 Classic(v2)/Unified(v3)
두 계정 모드의 실제 요청 조립(경로 + body 필드명)을 고정 픽스처로 검증.

`tests/integration/test_bitget_adapter.py`의 기존 테스트들은 모두 기본값
(CLASSIC)만 다루므로 그대로 통과해야 한다(이 리프가 기존 동작을 바꾸지
않는다는 회귀 보장) — 이 파일은 UNIFIED 분기와 40085 자동 전환만 추가로
다룬다.
"""
from __future__ import annotations

import json
from decimal import Decimal

import httpx
import pytest

from src.core.exceptions import FatalExchangeError
from src.data.models.base import AssetClass
from src.data.models.trading import Order, OrderSide, OrderType
from src.exchanges.bitget.account_mode import BitgetAccountMode
from src.exchanges.bitget.adapter import BitgetAdapter


def _make_adapter(handler) -> BitgetAdapter:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(base_url="https://api.bitget.com", transport=transport)
    return BitgetAdapter("key", "secret", "passphrase", demo_mode=True, http_client=client)


def _json(payload: dict, status_code: int = 200) -> httpx.Response:
    return httpx.Response(status_code, json=payload)


def _order() -> Order:
    return Order(
        client_order_id="c-1",
        strategy_id="s-1",
        strategy_version="v1",
        symbol="BTC/USDT",
        exchange="bitget",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=Decimal("0.01"),
        price=None,
        asset_class=AssetClass.CRYPTO,
    )


async def test_get_balance_unified_mode_hits_v3_path_and_parses_locked() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v3/account/assets"
        return _json(
            {
                "code": "00000",
                "data": {"assets": [{"coin": "usdt", "available": "100", "locked": "5"}]},
            }
        )

    adapter = _make_adapter(handler)
    adapter.account_mode = BitgetAccountMode.UNIFIED

    balances = await adapter.get_balance()

    assert balances[0].asset == "USDT"
    assert balances[0].available == Decimal("100")
    assert balances[0].total == Decimal("105")


async def test_get_balance_switches_to_unified_after_40085_then_succeeds() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/api/v2/spot/account/assets":
            return _json({"code": "40085", "msg": "Unified Account mode", "data": {}})
        assert request.url.path == "/api/v3/account/assets"
        row = {"coin": "usdt", "available": "1", "locked": "0"}
        return _json({"code": "00000", "data": {"assets": [row]}})

    adapter = _make_adapter(handler)
    assert adapter.account_mode is BitgetAccountMode.CLASSIC

    balances = await adapter.get_balance()

    assert calls == ["/api/v2/spot/account/assets", "/api/v3/account/assets"]
    assert adapter.account_mode is BitgetAccountMode.UNIFIED
    assert balances[0].asset == "USDT"


async def test_get_account_info_unified_mode_hits_v3_path() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v3/account/info"
        return _json({"code": "00000", "data": {"userId": "u-1"}})

    adapter = _make_adapter(handler)
    adapter.account_mode = BitgetAccountMode.UNIFIED

    info = await adapter.get_account_info()

    assert info == {"userId": "u-1"}


async def test_get_account_info_classic_40085_raises_auth_not_unknown_when_no_recovery() -> None:
    """40085를 받으면 UNIFIED로 전환해 재시도하지만, v3에서도 실패하면(예:
    이 계정이 UTA도 아닌 완전히 잘못된 상황) 원래 예외가 그대로 전파돼야
    한다 — 조용히 삼켜지면 안 된다."""

    def handler(request: httpx.Request) -> httpx.Response:
        return _json({"code": "40085", "msg": "Unified Account mode", "data": {}})

    adapter = _make_adapter(handler)

    with pytest.raises(FatalExchangeError):
        await adapter.get_account_info()
    assert adapter.account_mode is BitgetAccountMode.UNIFIED


async def test_place_order_unified_mode_uses_qty_and_category_not_size() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v3/trade/place-order"
        body = json.loads(request.content)
        assert body["qty"] == "0.01"
        assert body["category"] == "SPOT"
        assert "size" not in body
        return _json({"code": "00000", "data": {"orderId": "999", "clientOid": "c-1"}})

    adapter = _make_adapter(handler)
    adapter.account_mode = BitgetAccountMode.UNIFIED

    result = await adapter.place_order(_order())

    assert result.exchange_order_id == "999"


async def test_cancel_order_unified_mode_adds_category() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v3/trade/cancel-order"
        body = json.loads(request.content)
        assert body == {"orderId": "999", "category": "SPOT"}
        return _json({"code": "00000", "data": {"orderId": "999"}})

    adapter = _make_adapter(handler)
    adapter.account_mode = BitgetAccountMode.UNIFIED

    assert await adapter.cancel_order("999") is True


async def test_get_order_unified_mode_hits_v3_path_and_normalizes_qty_to_size() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v3/trade/order-info"
        assert request.url.params["category"] == "SPOT"
        return _json(
            {
                "code": "00000",
                "data": [
                    {
                        "orderId": "999",
                        "clientOid": "c-1",
                        "symbol": "BTCUSDT",
                        "side": "buy",
                        "orderType": "limit",
                        "qty": "0.01",
                        "status": "live",
                    }
                ],
            }
        )

    adapter = _make_adapter(handler)
    adapter.account_mode = BitgetAccountMode.UNIFIED

    order = await adapter.get_order("999")

    assert order.exchange_order_id == "999"
    assert order.quantity == Decimal("0.01")
