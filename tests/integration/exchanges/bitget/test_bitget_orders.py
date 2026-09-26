"""6.11 — BitgetAdapter order lifecycle integration tests.

Split out of the former single `tests/integration/test_bitget_adapter.py`
(788 lines, task-4225) — this file covers place/cancel/modify/batch/plan
orders, row-to-order mapping, and wallet transfer.
"""

import json
from decimal import Decimal

import httpx

from src.data.models.base import AssetClass
from src.data.models.trading import Order, OrderSide, OrderStatus, OrderType


async def test_place_order_returns_order_with_exchange_id(make_adapter, json_response):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        body = json.loads(request.content)
        assert body["symbol"] == "BTCUSDT"
        assert body["side"] == "buy"
        return json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": {"orderId": "999", "clientOid": "c-1"},
            }
        )

    adapter = make_adapter(handler)
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

    assert result.exchange_order_id == "999"
    assert result.status == OrderStatus.SUBMITTED


async def test_cancel_order_returns_true_on_success(make_adapter, json_response):
    adapter = make_adapter(
        lambda request: json_response(
            {"code": "00000", "msg": "success", "requestTime": 1, "data": {"orderId": "999"}}
        )
    )
    assert await adapter.cancel_order("999") is True


async def test_row_to_order_parses_avg_price_and_timestamps(make_adapter, json_response, order_row):
    """FULL_AUDIT §2-B ③ 회귀 — priceAvg/cTime/uTime이 버려지지 않고
    average_fill_price/created_at/updated_at으로 반영돼야 한다."""

    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": order_row(
                    status="filled",
                    fillSize="0.01",
                    priceAvg="81234.5",
                    cTime="1700000000000",
                    uTime="1700000100000",
                ),
            }
        )

    adapter = make_adapter(handler)
    order = await adapter.get_order("999")

    assert order.average_fill_price is not None
    assert order.average_fill_price.amount == Decimal("81234.5")
    assert order.created_at.year == 2023
    assert order.updated_at > order.created_at


async def test_row_to_order_falls_back_to_price_when_no_avg_price(
    make_adapter, json_response, order_row
):
    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": order_row(price="80000"),
            }
        )

    adapter = make_adapter(handler)
    order = await adapter.get_order("999")

    assert order.average_fill_price is not None
    assert order.average_fill_price.amount == Decimal("80000")


async def test_row_to_order_leaves_avg_price_none_when_unfilled(
    make_adapter, json_response, order_row
):
    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": order_row(priceAvg="0"),
            }
        )

    adapter = make_adapter(handler)
    order = await adapter.get_order("999")

    assert order.average_fill_price is None


async def test_get_open_orders_parses_unfilled_orders_response(
    make_adapter, json_response, order_row
):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/spot/trade/unfilled-orders"
        assert request.url.params["symbol"] == "BTCUSDT"
        return json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [order_row(), order_row(orderId="1000", status="partially_filled")],
            }
        )

    adapter = make_adapter(handler)
    orders = await adapter.get_open_orders("BTC/USDT")

    assert len(orders) == 2
    assert orders[0].exchange_order_id == "999"
    assert orders[0].status == OrderStatus.ACKNOWLEDGED
    assert orders[1].status == OrderStatus.PARTIALLY_FILLED


async def test_get_order_history_parses_history_orders_response(
    make_adapter, json_response, order_row
):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/spot/trade/history-orders"
        assert request.url.params["limit"] == "50"
        return json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [order_row(status="filled", fillSize="0.01")],
            }
        )

    adapter = make_adapter(handler)
    orders = await adapter.get_order_history("BTC/USDT", limit=50)

    assert orders[0].status == OrderStatus.FILLED
    assert orders[0].filled_quantity == Decimal("0.01")


async def test_get_fills_returns_raw_rows(make_adapter, json_response):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/spot/trade/fills"
        assert request.url.params["orderId"] == "999"
        return json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [{"orderId": "999", "tradeId": "t-1", "price": "80000", "size": "0.01"}],
            }
        )

    adapter = make_adapter(handler)
    fills = await adapter.get_fills(order_id="999")

    assert fills == [{"orderId": "999", "tradeId": "t-1", "price": "80000", "size": "0.01"}]


async def test_modify_order_cancel_replaces_then_reconfirms(make_adapter, json_response, order_row):
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/api/v2/spot/trade/cancel-replace-order":
            body = json.loads(request.content)
            assert body["orderId"] == "999"
            assert body["price"] == "81000"
            return json_response(
                {
                    "code": "00000",
                    "msg": "success",
                    "requestTime": 1,
                    "data": {"orderId": "999", "clientOid": "c-1"},
                }
            )
        assert request.url.path == "/api/v2/spot/trade/orderInfo"
        return json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": order_row(price="81000"),
            }
        )

    adapter = make_adapter(handler)
    order = await adapter.modify_order("999", price=Decimal("81000"))

    assert calls == ["/api/v2/spot/trade/cancel-replace-order", "/api/v2/spot/trade/orderInfo"]
    assert order.exchange_order_id == "999"


async def test_place_batch_orders_maps_success_and_failure_by_client_oid(
    make_adapter, json_response, new_order
):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/spot/trade/batch-orders"
        body = json.loads(request.content)
        assert body["symbol"] == "BTCUSDT"
        assert len(body["orderList"]) == 2
        return json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": {
                    "successList": [{"clientOid": "c-1", "orderId": "1"}],
                    "failureList": [{"clientOid": "c-2", "errorMsg": "insufficient balance"}],
                },
            }
        )

    adapter = make_adapter(handler)
    orders = [new_order(client_order_id="c-1"), new_order(client_order_id="c-2")]

    result = await adapter.place_batch_orders(orders)

    assert result[0].exchange_order_id == "1"
    assert result[0].status == OrderStatus.SUBMITTED
    assert result[1].status == OrderStatus.REJECTED


async def test_place_batch_orders_empty_list_short_circuits(
    make_adapter, json_response, real_ticker_envelope
):
    adapter = make_adapter(lambda request: json_response(real_ticker_envelope))
    assert await adapter.place_batch_orders([]) == []


async def test_cancel_batch_orders_returns_true_on_success(make_adapter, json_response):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/spot/trade/batch-cancel-order"
        body = json.loads(request.content)
        assert body["orderIdList"] == [{"orderId": "1"}, {"orderId": "2"}]
        return json_response({"code": "00000", "msg": "success", "requestTime": 1, "data": {}})

    adapter = make_adapter(handler)
    assert await adapter.cancel_batch_orders(["1", "2"]) is True


async def test_place_plan_order_sends_trigger_price(make_adapter, json_response):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/spot/trade/place-plan-order"
        body = json.loads(request.content)
        assert body["triggerPrice"] == "75000"
        assert body["side"] == "sell"
        return json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": {"orderId": "p-1", "clientOid": "c-1"},
            }
        )

    adapter = make_adapter(handler)
    result = await adapter.place_plan_order(
        "BTC/USDT", OrderSide.SELL, Decimal("0.01"), Decimal("75000")
    )

    assert result == {"orderId": "p-1", "clientOid": "c-1"}


async def test_cancel_plan_order_returns_true_on_success(make_adapter, json_response):
    adapter = make_adapter(
        lambda request: json_response(
            {"code": "00000", "msg": "success", "requestTime": 1, "data": {"orderId": "p-1"}}
        )
    )
    assert await adapter.cancel_plan_order("p-1") is True


async def test_get_current_plan_orders_returns_raw_rows(make_adapter, json_response):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/spot/trade/current-plan-order"
        return json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [{"orderId": "p-1", "triggerPrice": "75000"}],
            }
        )

    adapter = make_adapter(handler)
    orders = await adapter.get_current_plan_orders("BTC/USDT")

    assert orders == [{"orderId": "p-1", "triggerPrice": "75000"}]


async def test_transfer_returns_true_on_success(make_adapter, json_response):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/spot/wallet/transfer"
        body = json.loads(request.content)
        assert body == {
            "fromType": "spot",
            "toType": "usdt_futures",
            "amount": "100",
            "coin": "USDT",
        }
        return json_response({"code": "00000", "msg": "success", "requestTime": 1, "data": {}})

    adapter = make_adapter(handler)
    result = await adapter.transfer("spot", "usdt_futures", Decimal("100"), "usdt")

    assert result is True


async def test_get_history_plan_orders_returns_raw_rows(make_adapter, json_response):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/spot/trade/history-plan-order"
        assert request.url.params["symbol"] == "BTCUSDT"
        assert request.url.params["limit"] == "50"
        return json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [
                    {
                        "orderId": "p-1",
                        "symbol": "BTCUSDT",
                        "triggerPrice": "75000",
                        "status": "success",
                    }
                ],
            }
        )

    adapter = make_adapter(handler)
    orders = await adapter.get_history_plan_orders("BTC/USDT", limit=50)

    assert len(orders) == 1
    assert orders[0]["orderId"] == "p-1"
    assert orders[0]["status"] == "success"


async def test_modify_plan_order_updates_trigger_price(make_adapter, json_response):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/spot/trade/modify-plan-order"
        body = json.loads(request.content)
        assert body["orderId"] == "p-1"
        assert body["triggerPrice"] == "76000"
        return json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": {"orderId": "p-1", "triggerPrice": "76000"},
            }
        )

    adapter = make_adapter(handler)
    result = await adapter.modify_plan_order("p-1", trigger_price=Decimal("76000"))

    assert result["orderId"] == "p-1"
    assert result["triggerPrice"] == "76000"


async def test_batch_cancel_plan_orders_returns_true_on_success(make_adapter, json_response):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/spot/trade/batch-cancel-plan-order"
        body = json.loads(request.content)
        assert body["orderIdList"] == [{"orderId": "p-1"}, {"orderId": "p-2"}]
        assert body["symbol"] == "BTCUSDT"
        return json_response({"code": "00000", "msg": "success", "requestTime": 1, "data": {}})

    adapter = make_adapter(handler)
    result = await adapter.batch_cancel_plan_orders(["p-1", "p-2"], symbol="BTC/USDT")

    assert result is True


async def test_batch_cancel_replace_orders_returns_data(make_adapter, json_response):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/spot/trade/batch-cancel-replace-order"
        body = json.loads(request.content)
        assert body["orderIdList"] == [{"orderId": "999"}]
        return json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": {"successList": [{"orderId": "1000"}]},
            }
        )

    adapter = make_adapter(handler)
    result = await adapter.batch_cancel_replace_orders(["999"])

    assert result == {"successList": [{"orderId": "1000"}]}


async def test_batch_cancel_replace_orders_sends_cancel_only_no_replace_fields(
    make_adapter, json_response
):
    """Contract: despite the endpoint name, this method is cancel-only — it
    must never fabricate replacement price/size fields (undocumented batch
    schema, see docstring on `batch_cancel_replace_orders`)."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert set(body.keys()) == {"orderIdList", "symbol"}
        for entry in body["orderIdList"]:
            assert set(entry.keys()) == {"orderId"}
            assert "price" not in entry
            assert "size" not in entry
        return json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": {"successList": [{"orderId": "1000"}]},
            }
        )

    adapter = make_adapter(handler)
    await adapter.batch_cancel_replace_orders(["999"], symbol="BTC/USDT")


async def test_cancel_symbol_orders_returns_true_on_success(make_adapter, json_response):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/spot/trade/cancel-symbol-order"
        body = json.loads(request.content)
        assert body["symbol"] == "BTCUSDT"
        return json_response({"code": "00000", "msg": "success", "requestTime": 1, "data": {}})

    adapter = make_adapter(handler)
    result = await adapter.cancel_symbol_orders("BTC/USDT")

    assert result is True


# ---------- task-6876 QA — negative: empty-data response ----------


async def test_get_history_plan_orders_returns_empty_list_on_empty_data(
    make_adapter, json_response
):
    adapter = make_adapter(
        lambda request: json_response(
            {"code": "00000", "msg": "success", "requestTime": 1, "data": []}
        )
    )
    assert await adapter.get_history_plan_orders("BTC/USDT") == []
