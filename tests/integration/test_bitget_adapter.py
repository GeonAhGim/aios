"""6.11 — BitgetAdapter 통합 테스트.

실제 Bitget Demo 계정 API 키가 없는 상태(.env BITGET_API_KEY 비어있음)라
httpx.MockTransport로 실제 캡처한 Bitget 응답 형태를 재현해 검증한다.
실제 Demo 계좌 왕복 테스트(주문 생성→조회→취소)는 사용자가 API 키를
채운 뒤 별도로 수행해야 한다(08번 §8.3 원본 의도).
"""
import json
from decimal import Decimal

import httpx
import pytest

from src.core.exceptions import FatalExchangeError, RetryableExchangeError
from src.data.models.base import AssetClass
from src.data.models.trading import Order, OrderSide, OrderStatus, OrderType
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


def _make_adapter(handler) -> BitgetAdapter:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(base_url="https://api.bitget.com", transport=transport)
    return BitgetAdapter("key", "secret", "passphrase", demo_mode=True, http_client=client)


def _json_response(payload: dict, status_code: int = 200) -> httpx.Response:
    return httpx.Response(status_code, json=payload)


async def test_get_ticker_parses_real_response_shape():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/spot/market/tickers"
        assert request.headers["ACCESS-KEY"] == "key"
        assert request.headers["paptrading"] == "1"
        return _json_response(REAL_TICKER_ENVELOPE)

    adapter = _make_adapter(handler)
    ticker = await adapter.get_ticker("BTC/USDT")

    assert ticker.symbol == "BTC/USDT"
    assert ticker.price == Decimal("80663.08")


async def test_capabilities_declare_crypto_only():
    adapter = _make_adapter(lambda request: _json_response(REAL_TICKER_ENVELOPE))
    caps = adapter.get_capabilities()

    assert caps.supported_asset_classes == [AssetClass.CRYPTO]
    assert caps.supports_futures is False


async def test_api_error_response_raises_retryable_by_default():
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response({"code": "99999", "msg": "internal error", "data": {}})

    adapter = _make_adapter(handler)
    with pytest.raises(RetryableExchangeError):
        await adapter.get_ticker("BTC/USDT")


async def test_signature_error_code_raises_fatal():
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response({"code": "40012", "msg": "invalid sign", "data": {}})

    adapter = _make_adapter(handler)
    with pytest.raises(FatalExchangeError):
        await adapter.get_ticker("BTC/USDT")


async def test_get_balance_maps_coin_amounts():
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [
                    {"coin": "usdt", "available": "100", "frozen": "5", "locked": "0"},
                ],
            }
        )

    adapter = _make_adapter(handler)
    balances = await adapter.get_balance()

    assert balances[0].asset == "USDT"
    assert balances[0].total == Decimal("105")
    assert balances[0].available == Decimal("100")


async def test_get_positions_always_empty_for_spot():
    adapter = _make_adapter(lambda request: _json_response(REAL_TICKER_ENVELOPE))
    assert await adapter.get_positions() == []


async def test_place_order_returns_order_with_exchange_id():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        body = json.loads(request.content)
        assert body["symbol"] == "BTCUSDT"
        assert body["side"] == "buy"
        return _json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": {"orderId": "999", "clientOid": "c-1"},
            }
        )

    adapter = _make_adapter(handler)
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


async def test_cancel_order_returns_true_on_success():
    adapter = _make_adapter(
        lambda request: _json_response(
            {"code": "00000", "msg": "success", "requestTime": 1, "data": {"orderId": "999"}}
        )
    )
    assert await adapter.cancel_order("999") is True


async def test_health_check_returns_false_on_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response({"code": "99999", "msg": "error", "data": {}})

    adapter = _make_adapter(handler)
    assert await adapter.health_check() is False


async def test_health_check_returns_true_on_success():
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response({"code": "00000", "msg": "success", "requestTime": 1, "data": []})

    adapter = _make_adapter(handler)
    assert await adapter.health_check() is True


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


async def test_get_open_orders_parses_unfilled_orders_response():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/spot/trade/unfilled-orders"
        assert request.url.params["symbol"] == "BTCUSDT"
        return _json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [_order_row(), _order_row(orderId="1000", status="partially_filled")],
            }
        )

    adapter = _make_adapter(handler)
    orders = await adapter.get_open_orders("BTC/USDT")

    assert len(orders) == 2
    assert orders[0].exchange_order_id == "999"
    assert orders[0].status == OrderStatus.ACKNOWLEDGED
    assert orders[1].status == OrderStatus.PARTIALLY_FILLED


async def test_get_order_history_parses_history_orders_response():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/spot/trade/history-orders"
        assert request.url.params["limit"] == "50"
        return _json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [_order_row(status="filled", fillSize="0.01")],
            }
        )

    adapter = _make_adapter(handler)
    orders = await adapter.get_order_history("BTC/USDT", limit=50)

    assert orders[0].status == OrderStatus.FILLED
    assert orders[0].filled_quantity == Decimal("0.01")


async def test_get_fills_returns_raw_rows():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/spot/trade/fills"
        assert request.url.params["orderId"] == "999"
        return _json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [{"orderId": "999", "tradeId": "t-1", "price": "80000", "size": "0.01"}],
            }
        )

    adapter = _make_adapter(handler)
    fills = await adapter.get_fills(order_id="999")

    assert fills == [{"orderId": "999", "tradeId": "t-1", "price": "80000", "size": "0.01"}]


async def test_modify_order_cancel_replaces_then_reconfirms():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/api/v2/spot/trade/cancel-replace-order":
            body = json.loads(request.content)
            assert body["orderId"] == "999"
            assert body["price"] == "81000"
            return _json_response(
                {
                    "code": "00000",
                    "msg": "success",
                    "requestTime": 1,
                    "data": {"orderId": "999", "clientOid": "c-1"},
                }
            )
        assert request.url.path == "/api/v2/spot/trade/orderInfo"
        return _json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": _order_row(price="81000"),
            }
        )

    adapter = _make_adapter(handler)
    order = await adapter.modify_order("999", price=Decimal("81000"))

    assert calls == ["/api/v2/spot/trade/cancel-replace-order", "/api/v2/spot/trade/orderInfo"]
    assert order.exchange_order_id == "999"


async def test_get_history_candles_parses_rows():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/spot/market/history-candles"
        assert request.url.params["endTime"] == "1700000000000"
        return _json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [["1700000000000", "80000", "81000", "79500", "80500", "12.3"]],
            }
        )

    adapter = _make_adapter(handler)
    candles = await adapter.get_history_candles("BTC/USDT", "1h", end_time="1700000000000")

    assert candles[0].close == Decimal("80500")
    assert candles[0].timeframe == "1h"


async def test_get_symbol_info_derives_tick_and_lot_from_precision():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/spot/public/symbols"
        return _json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [
                    {
                        "baseCoin": "BTC",
                        "quoteCoin": "USDT",
                        "pricePrecision": "2",
                        "quantityPrecision": "4",
                        "minTradeAmount": "0.0001",
                        "status": "online",
                    }
                ],
            }
        )

    adapter = _make_adapter(handler)
    symbols = await adapter.get_symbol_info()

    assert symbols[0].symbol == "BTC/USDT"
    assert symbols[0].tick_size == Decimal("0.01")
    assert symbols[0].lot_size == Decimal("0.0001")
    assert symbols[0].status == "online"


async def test_get_public_trades_parses_fills():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/spot/market/fills"
        return _json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [
                    {
                        "tradeId": "t-1",
                        "price": "80000",
                        "size": "0.5",
                        "side": "buy",
                        "ts": "1700000000000",
                    }
                ],
            }
        )

    adapter = _make_adapter(handler)
    trades = await adapter.get_public_trades("BTC/USDT")

    assert trades[0].trade_id == "t-1"
    assert trades[0].price == Decimal("80000")
    assert trades[0].side == "buy"


def _new_order(**overrides: object) -> Order:
    defaults: dict[str, object] = {
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
    return Order(**defaults)  # type: ignore[arg-type]


async def test_place_batch_orders_maps_success_and_failure_by_client_oid():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/spot/trade/batch-orders"
        body = json.loads(request.content)
        assert body["symbol"] == "BTCUSDT"
        assert len(body["orderList"]) == 2
        return _json_response(
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

    adapter = _make_adapter(handler)
    orders = [_new_order(client_order_id="c-1"), _new_order(client_order_id="c-2")]

    result = await adapter.place_batch_orders(orders)

    assert result[0].exchange_order_id == "1"
    assert result[0].status == OrderStatus.SUBMITTED
    assert result[1].status == OrderStatus.REJECTED


async def test_place_batch_orders_empty_list_short_circuits():
    adapter = _make_adapter(lambda request: _json_response(REAL_TICKER_ENVELOPE))
    assert await adapter.place_batch_orders([]) == []


async def test_cancel_batch_orders_returns_true_on_success():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/spot/trade/batch-cancel-order"
        body = json.loads(request.content)
        assert body["orderIdList"] == [{"orderId": "1"}, {"orderId": "2"}]
        return _json_response({"code": "00000", "msg": "success", "requestTime": 1, "data": {}})

    adapter = _make_adapter(handler)
    assert await adapter.cancel_batch_orders(["1", "2"]) is True


async def test_place_plan_order_sends_trigger_price():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/spot/trade/place-plan-order"
        body = json.loads(request.content)
        assert body["triggerPrice"] == "75000"
        assert body["side"] == "sell"
        return _json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": {"orderId": "p-1", "clientOid": "c-1"},
            }
        )

    adapter = _make_adapter(handler)
    result = await adapter.place_plan_order(
        "BTC/USDT", OrderSide.SELL, Decimal("0.01"), Decimal("75000")
    )

    assert result == {"orderId": "p-1", "clientOid": "c-1"}


async def test_cancel_plan_order_returns_true_on_success():
    adapter = _make_adapter(
        lambda request: _json_response(
            {"code": "00000", "msg": "success", "requestTime": 1, "data": {"orderId": "p-1"}}
        )
    )
    assert await adapter.cancel_plan_order("p-1") is True


async def test_get_current_plan_orders_returns_raw_rows():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/spot/trade/current-plan-order"
        return _json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [{"orderId": "p-1", "triggerPrice": "75000"}],
            }
        )

    adapter = _make_adapter(handler)
    orders = await adapter.get_current_plan_orders("BTC/USDT")

    assert orders == [{"orderId": "p-1", "triggerPrice": "75000"}]


async def test_get_account_info_returns_raw_dict():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/spot/account/info"
        return _json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": {"userId": "u-1", "authorities": ["spot_trade"]},
            }
        )

    adapter = _make_adapter(handler)
    info = await adapter.get_account_info()

    assert info == {"userId": "u-1", "authorities": ["spot_trade"]}


async def test_get_account_bills_returns_raw_rows():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/spot/account/bills"
        assert request.url.params["coin"] == "USDT"
        return _json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [{"billId": "b-1", "coin": "USDT", "amount": "10"}],
            }
        )

    adapter = _make_adapter(handler)
    bills = await adapter.get_account_bills("USDT")

    assert bills == [{"billId": "b-1", "coin": "USDT", "amount": "10"}]


async def test_get_server_time_parses_timestamp():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/public/time"
        return _json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": {"serverTime": "1700000000000"},
            }
        )

    adapter = _make_adapter(handler)
    server_time = await adapter.get_server_time()

    assert server_time.year == 2023


async def test_get_trade_rate_returns_raw_dict():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/common/trade-rate"
        assert request.url.params["symbol"] == "BTCUSDT"
        return _json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": {"makerFeeRate": "0.001", "takerFeeRate": "0.001"},
            }
        )

    adapter = _make_adapter(handler)
    rate = await adapter.get_trade_rate("BTC/USDT")

    assert rate == {"makerFeeRate": "0.001", "takerFeeRate": "0.001"}


async def test_transfer_returns_true_on_success():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/spot/wallet/transfer"
        body = json.loads(request.content)
        assert body == {
            "fromType": "spot",
            "toType": "usdt_futures",
            "amount": "100",
            "coin": "USDT",
        }
        return _json_response({"code": "00000", "msg": "success", "requestTime": 1, "data": {}})

    adapter = _make_adapter(handler)
    result = await adapter.transfer("spot", "usdt_futures", Decimal("100"), "usdt")

    assert result is True
