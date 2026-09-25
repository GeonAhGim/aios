"""02b_bitget_api_v2_full_spec_v1.md §5 통합테스트 — Futures/Mix P0 (Order).

실제 Bitget Demo 계정 API 키가 없는 상태라 httpx.MockTransport로 응답
형태를 재현해 검증한다(test_bitget_adapter.py와 동일 원칙) — 필드명은
커뮤니티 SDK 레퍼런스 기준 최선 추정치라 라이브 검증 전까지는 확정 아님.
"""

import json
from decimal import Decimal

import httpx
import pytest

from src.core.exceptions import FrozenZonePaperAdapterBlockedError
from src.data.models.trading import OrderStatus
from src.exchanges.bitget.adapter import BitgetAdapter
from tests.integration.bitget_futures_doubles import json_response, make_adapter, make_order


async def test_place_futures_order():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/mix/order/place-order"
        body = json.loads(request.content)
        assert body["marginMode"] == "crossed"
        return json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": {"orderId": "777", "clientOid": "c-1"},
            }
        )

    adapter = make_adapter(handler)
    result = await adapter.place_futures_order(make_order())

    assert result.exchange_order_id == "777"
    assert result.status == OrderStatus.SUBMITTED


async def test_place_futures_order_blocked_on_live_configured_adapter():
    """레드팀 #2026-09-02-32 회귀 테스트."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("가드가 막았어야 할 요청이 실제로 나갔습니다.")

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(base_url="https://api.bitget.com", transport=transport)
    live_adapter = BitgetAdapter("key", "secret", "passphrase", demo_mode=False, http_client=client)

    with pytest.raises(FrozenZonePaperAdapterBlockedError):
        await live_adapter.place_futures_order(make_order())


async def test_cancel_futures_order():
    adapter = make_adapter(
        lambda request: json_response(
            {"code": "00000", "msg": "success", "requestTime": 1, "data": {"orderId": "777"}}
        )
    )
    assert await adapter.cancel_futures_order("777", symbol="BTC/USDT") is True


async def test_close_futures_position():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/mix/order/close-positions"
        return json_response({"code": "00000", "msg": "success", "requestTime": 1, "data": {}})

    adapter = make_adapter(handler)
    assert await adapter.close_futures_position("BTC/USDT") is True


async def test_get_futures_order():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/mix/order/detail"
        return json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": {
                    "orderId": "777",
                    "clientOid": "c-1",
                    "symbol": "BTCUSDT",
                    "side": "buy",
                    "orderType": "market",
                    "size": "0.01",
                    "state": "filled",
                    "baseVolume": "0.01",
                },
            }
        )

    adapter = make_adapter(handler)
    order = await adapter.get_futures_order("777", symbol="BTC/USDT")

    assert order.status == OrderStatus.FILLED


async def test_modify_futures_order():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/api/v2/mix/order/modify-order":
            body = json.loads(request.content)
            assert body["newPrice"] == "82000"
            return json_response(
                {
                    "code": "00000",
                    "msg": "success",
                    "requestTime": 1,
                    "data": {"orderId": "777"},
                }
            )
        return json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": {"orderId": "777", "symbol": "BTCUSDT", "side": "buy", "state": "live"},
            }
        )

    adapter = make_adapter(handler)
    order = await adapter.modify_futures_order("777", symbol="BTC/USDT", price=Decimal("82000"))

    assert calls == ["/api/v2/mix/order/modify-order", "/api/v2/mix/order/detail"]
    assert order.exchange_order_id == "777"


async def test_get_futures_open_orders():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/mix/order/orders-pending"
        return json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": {
                    "entrustedList": [
                        {"orderId": "777", "symbol": "BTCUSDT", "side": "buy", "state": "live"}
                    ]
                },
            }
        )

    adapter = make_adapter(handler)
    orders = await adapter.get_futures_open_orders()

    assert orders[0].exchange_order_id == "777"


async def test_get_futures_open_orders_handles_null_entrusted_list():
    """Bitget이 미체결 주문이 없을 때 entrustedList를 null로 반환하는
    케이스도 안전하게 빈 리스트로 처리한다."""
    adapter = make_adapter(
        lambda request: json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": {"entrustedList": None},
            }
        )
    )
    assert await adapter.get_futures_open_orders() == []


async def test_get_futures_fills():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/mix/order/fills"
        return json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": {"fillList": [{"orderId": "777", "price": "81000", "baseVolume": "0.01"}]},
            }
        )

    adapter = make_adapter(handler)
    fills = await adapter.get_futures_fills()

    assert fills == [{"orderId": "777", "price": "81000", "baseVolume": "0.01"}]


async def test_cancel_all_futures_orders_returns_true_on_success():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/mix/order/cancel-all-orders"
        return json_response({"code": "00000", "msg": "success", "requestTime": 1, "data": {}})

    adapter = make_adapter(handler)
    assert await adapter.cancel_all_futures_orders(symbol="BTC/USDT") is True


async def test_place_futures_tpsl_order_sends_trigger_price():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/mix/order/place-tpsl-order"
        body = json.loads(request.content)
        assert body["triggerPrice"] == "85000"
        assert body["planType"] == "profit_plan"
        return json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": {"orderId": "tp-1"},
            }
        )

    adapter = make_adapter(handler)
    result = await adapter.place_futures_tpsl_order("BTC/USDT", "profit_plan", Decimal("85000"))

    assert result == {"orderId": "tp-1"}


async def test_place_futures_position_tpsl_requires_at_least_one_trigger():
    adapter = make_adapter(lambda request: json_response({"code": "00000", "data": {}}))
    with pytest.raises(ValueError):
        await adapter.place_futures_position_tpsl("BTC/USDT")


async def test_place_futures_position_tpsl_sends_both_triggers():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/mix/order/place-pos-tpsl"
        body = json.loads(request.content)
        assert body["stopSurplusTriggerPrice"] == "90000"
        assert body["stopLossTriggerPrice"] == "70000"
        return json_response(
            {"code": "00000", "msg": "success", "requestTime": 1, "data": {"orderId": "pos-tp-1"}}
        )

    adapter = make_adapter(handler)
    result = await adapter.place_futures_position_tpsl(
        "BTC/USDT", take_profit_trigger=Decimal("90000"), stop_loss_trigger=Decimal("70000")
    )

    assert result == {"orderId": "pos-tp-1"}


async def test_place_futures_plan_order_sends_trigger_price():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/mix/order/place-plan-order"
        body = json.loads(request.content)
        assert body["triggerPrice"] == "75000"
        return json_response(
            {"code": "00000", "msg": "success", "requestTime": 1, "data": {"orderId": "plan-1"}}
        )

    adapter = make_adapter(handler)
    result = await adapter.place_futures_plan_order(make_order(), Decimal("75000"))

    assert result == {"orderId": "plan-1"}


async def test_cancel_futures_plan_order_returns_true_on_success():
    adapter = make_adapter(
        lambda request: json_response(
            {"code": "00000", "msg": "success", "requestTime": 1, "data": {}}
        )
    )
    assert await adapter.cancel_futures_plan_order("plan-1", symbol="BTC/USDT") is True


async def test_get_futures_current_plan_orders():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/mix/order/orders-plan-pending"
        return json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": {"entrustedList": [{"orderId": "plan-1", "triggerPrice": "75000"}]},
            }
        )

    adapter = make_adapter(handler)
    orders = await adapter.get_futures_current_plan_orders(symbol="BTC/USDT")

    assert orders == [{"orderId": "plan-1", "triggerPrice": "75000"}]


async def test_get_futures_current_plan_orders_handles_null_entrusted_list():
    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(
            {"code": "00000", "msg": "success", "requestTime": 1, "data": {"entrustedList": None}}
        )

    adapter = make_adapter(handler)
    assert await adapter.get_futures_current_plan_orders() == []


async def test_place_futures_batch_orders():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/mix/order/batch-place-order"
        body = json.loads(request.content)
        assert body["marginMode"] == "crossed"
        assert len(body["orderList"]) == 1
        return json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": {
                    "successList": [{"orderId": "888", "clientOid": "c-1"}],
                    "failureList": [],
                },
            }
        )

    adapter = make_adapter(handler)
    result = await adapter.place_futures_batch_orders([make_order()])

    assert result[0].exchange_order_id == "888"
    assert result[0].status == OrderStatus.SUBMITTED


async def test_place_futures_batch_orders_marks_failures_rejected():
    order = make_order()

    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": {
                    "successList": [],
                    "failureList": [{"clientOid": order.client_order_id, "errorMsg": "denied"}],
                },
            }
        )

    adapter = make_adapter(handler)
    result = await adapter.place_futures_batch_orders([order])

    assert result[0].status == OrderStatus.REJECTED


async def test_place_futures_batch_orders_empty_list_short_circuits():
    adapter = make_adapter(
        lambda request: (_ for _ in ()).throw(
            AssertionError("빈 리스트는 요청을 보내면 안 됩니다.")
        )
    )
    assert await adapter.place_futures_batch_orders([]) == []


async def test_place_futures_batch_orders_blocked_on_live_configured_adapter():
    """레드팀 #2026-09-02-32 회귀 테스트(place_futures_order와 동일 가드)."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("가드가 막았어야 할 요청이 실제로 나갔습니다.")

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(base_url="https://api.bitget.com", transport=transport)
    live_adapter = BitgetAdapter("key", "secret", "passphrase", demo_mode=False, http_client=client)

    with pytest.raises(FrozenZonePaperAdapterBlockedError):
        await live_adapter.place_futures_batch_orders([make_order()])


async def test_cancel_futures_batch_orders_with_explicit_ids():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/mix/order/batch-cancel-orders"
        body = json.loads(request.content)
        assert body["orderIdList"] == [{"orderId": "1"}, {"orderId": "2"}]
        return json_response({"code": "00000", "msg": "success", "requestTime": 1, "data": {}})

    adapter = make_adapter(handler)
    assert await adapter.cancel_futures_batch_orders(["1", "2"], symbol="BTC/USDT") is True


async def test_cancel_futures_batch_orders_without_ids_cancels_all_for_symbol():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert "orderIdList" not in body
        assert body["symbol"] == "BTCUSDT"
        return json_response({"code": "00000", "msg": "success", "requestTime": 1, "data": {}})

    adapter = make_adapter(handler)
    assert await adapter.cancel_futures_batch_orders(symbol="BTC/USDT") is True


async def test_cancel_futures_batch_orders_blocked_on_live_configured_adapter():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("가드가 막았어야 할 요청이 실제로 나갔습니다.")

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(base_url="https://api.bitget.com", transport=transport)
    live_adapter = BitgetAdapter("key", "secret", "passphrase", demo_mode=False, http_client=client)

    with pytest.raises(FrozenZonePaperAdapterBlockedError):
        await live_adapter.cancel_futures_batch_orders(symbol="BTC/USDT")
