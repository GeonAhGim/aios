"""02b_bitget_api_v2_full_spec_v1.md §5 통합테스트 — Futures/Mix P0.

실제 Bitget Demo 계정 API 키가 없는 상태라 httpx.MockTransport로 응답
형태를 재현해 검증한다(test_bitget_adapter.py와 동일 원칙) — 필드명은
커뮤니티 SDK 레퍼런스 기준 최선 추정치라 라이브 검증 전까지는 확정 아님.
"""
import json
from decimal import Decimal

import httpx
import pytest

from src.data.models.base import AssetClass
from src.data.models.trading import Order, OrderSide, OrderStatus, OrderType
from src.exchanges.bitget.adapter import BitgetAdapter


def _make_adapter(handler) -> BitgetAdapter:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(base_url="https://api.bitget.com", transport=transport)
    return BitgetAdapter("key", "secret", "passphrase", demo_mode=True, http_client=client)


def _json_response(payload: dict, status_code: int = 200) -> httpx.Response:
    return httpx.Response(status_code, json=payload)


def _order() -> Order:
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


# ---------- Market ----------


async def test_get_futures_contracts():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/mix/market/contracts"
        assert request.url.params["productType"] == "USDT-FUTURES"
        return _json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [
                    {
                        "symbol": "BTCUSDT",
                        "baseCoin": "BTC",
                        "quoteCoin": "USDT",
                        "minTradeNum": "0.001",
                        "priceEndStep": "0.1",
                        "volumePlace": "3",
                        "maxLever": "125",
                    }
                ],
            }
        )

    adapter = _make_adapter(handler)
    contracts = await adapter.get_futures_contracts()

    assert contracts[0].symbol == "BTCUSDT"
    assert contracts[0].max_leverage == Decimal("125")


async def test_get_futures_ticker():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/mix/market/ticker"
        return _json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [{"lastPr": "81000", "bidPr": "80999", "askPr": "81001"}],
            }
        )

    adapter = _make_adapter(handler)
    ticker = await adapter.get_futures_ticker("BTC/USDT")

    assert ticker.price == Decimal("81000")


async def test_get_futures_orderbook():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/mix/market/merge-depth"
        return _json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": {"bids": [["80900", "1.5"]], "asks": [["80950", "2.0"]]},
            }
        )

    adapter = _make_adapter(handler)
    book = await adapter.get_futures_orderbook("BTC/USDT")

    assert book.bids[0].price == Decimal("80900")
    assert book.asks[0].quantity == Decimal("2.0")


async def test_get_futures_candles():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/mix/market/candles"
        assert request.url.params["granularity"] == "1H"
        return _json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [["1700000000000", "80000", "81000", "79500", "80500", "12.3"]],
            }
        )

    adapter = _make_adapter(handler)
    candles = await adapter.get_futures_candles("BTC/USDT", "1h")

    assert candles[0].close == Decimal("80500")


async def test_get_futures_current_funding_rate():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/mix/market/current-fund-rate"
        return _json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [{"fundingRate": "0.0001", "nextUpdate": "1700003600000"}],
            }
        )

    adapter = _make_adapter(handler)
    rate = await adapter.get_futures_current_funding_rate("BTC/USDT")

    assert rate.current_rate == Decimal("0.0001")


# ---------- Account / Position ----------


async def test_get_futures_accounts():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/mix/account/accounts"
        return _json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [
                    {
                        "marginCoin": "usdt",
                        "available": "1000",
                        "accountEquity": "1200",
                        "locked": "0",
                    }
                ],
            }
        )

    adapter = _make_adapter(handler)
    balances = await adapter.get_futures_accounts()

    assert balances[0].asset == "USDT"
    assert balances[0].total == Decimal("1200")


async def test_set_futures_leverage():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/mix/account/set-leverage"
        body = json.loads(request.content)
        assert body["leverage"] == "5"
        return _json_response({"code": "00000", "msg": "success", "requestTime": 1, "data": {}})

    adapter = _make_adapter(handler)
    await adapter.set_futures_leverage("BTC/USDT", Decimal("5"))


async def test_set_futures_margin_mode_rejects_invalid_value():
    adapter = _make_adapter(lambda request: _json_response({"code": "00000", "data": {}}))
    try:
        await adapter.set_futures_margin_mode("BTC/USDT", "bogus")
        raise AssertionError("ValueError를 던졌어야 함")
    except ValueError:
        pass


async def test_get_futures_liquidation_price():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/mix/account/liq-price"
        return _json_response(
            {"code": "00000", "msg": "success", "requestTime": 1, "data": {"liqPx": "70000"}}
        )

    adapter = _make_adapter(handler)
    price = await adapter.get_futures_liquidation_price("BTC/USDT")

    assert price == Decimal("70000")


async def test_get_futures_position_returns_none_when_empty():
    adapter = _make_adapter(
        lambda request: _json_response(
            {"code": "00000", "msg": "success", "requestTime": 1, "data": []}
        )
    )
    assert await adapter.get_futures_position("BTC/USDT") is None


async def test_get_futures_position_parses_row():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/mix/position/single-position"
        return _json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [
                    {
                        "total": "0.5",
                        "openPriceAvg": "80000",
                        "markPrice": "81000",
                        "unrealizedPL": "500",
                        "achievedProfits": "0",
                        "leverage": "10",
                        "marginSize": "4000",
                    }
                ],
            }
        )

    adapter = _make_adapter(handler)
    position = await adapter.get_futures_position("BTC/USDT")

    assert position is not None
    assert position.quantity == Decimal("0.5")
    assert position.leverage == Decimal("10")


async def test_get_futures_positions():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/mix/position/all-position"
        return _json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [{"symbol": "BTCUSDT", "total": "0.5", "openPriceAvg": "80000"}],
            }
        )

    adapter = _make_adapter(handler)
    positions = await adapter.get_futures_positions()

    assert positions[0].symbol == "BTCUSDT"


# ---------- Order ----------


async def test_place_futures_order():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/mix/order/place-order"
        body = json.loads(request.content)
        assert body["marginMode"] == "crossed"
        return _json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": {"orderId": "777", "clientOid": "c-1"},
            }
        )

    adapter = _make_adapter(handler)
    result = await adapter.place_futures_order(_order())

    assert result.exchange_order_id == "777"
    assert result.status == OrderStatus.SUBMITTED


async def test_cancel_futures_order():
    adapter = _make_adapter(
        lambda request: _json_response(
            {"code": "00000", "msg": "success", "requestTime": 1, "data": {"orderId": "777"}}
        )
    )
    assert await adapter.cancel_futures_order("777", symbol="BTC/USDT") is True


async def test_close_futures_position():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/mix/order/close-positions"
        return _json_response({"code": "00000", "msg": "success", "requestTime": 1, "data": {}})

    adapter = _make_adapter(handler)
    assert await adapter.close_futures_position("BTC/USDT") is True


async def test_get_futures_order():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/mix/order/detail"
        return _json_response(
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

    adapter = _make_adapter(handler)
    order = await adapter.get_futures_order("777", symbol="BTC/USDT")

    assert order.status == OrderStatus.FILLED


async def test_modify_futures_order():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/api/v2/mix/order/modify-order":
            body = json.loads(request.content)
            assert body["newPrice"] == "82000"
            return _json_response(
                {
                    "code": "00000",
                    "msg": "success",
                    "requestTime": 1,
                    "data": {"orderId": "777"},
                }
            )
        return _json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": {"orderId": "777", "symbol": "BTCUSDT", "side": "buy", "state": "live"},
            }
        )

    adapter = _make_adapter(handler)
    order = await adapter.modify_futures_order("777", symbol="BTC/USDT", price=Decimal("82000"))

    assert calls == ["/api/v2/mix/order/modify-order", "/api/v2/mix/order/detail"]
    assert order.exchange_order_id == "777"


async def test_get_futures_open_orders():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/mix/order/orders-pending"
        return _json_response(
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

    adapter = _make_adapter(handler)
    orders = await adapter.get_futures_open_orders()

    assert orders[0].exchange_order_id == "777"


async def test_get_futures_open_orders_handles_null_entrusted_list():
    """Bitget이 미체결 주문이 없을 때 entrustedList를 null로 반환하는
    케이스도 안전하게 빈 리스트로 처리한다."""
    adapter = _make_adapter(
        lambda request: _json_response(
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
        return _json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": {"fillList": [{"orderId": "777", "price": "81000", "baseVolume": "0.01"}]},
            }
        )

    adapter = _make_adapter(handler)
    fills = await adapter.get_futures_fills()

    assert fills == [{"orderId": "777", "price": "81000", "baseVolume": "0.01"}]


async def test_get_futures_history_funding_rate():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/mix/market/history-fund-rate"
        return _json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [{"fundingRate": "0.0001", "fundingTime": "1700000000000"}],
            }
        )

    adapter = _make_adapter(handler)
    rates = await adapter.get_futures_history_funding_rate("BTC/USDT")

    assert rates[0].current_rate == Decimal("0.0001")


async def test_get_futures_funding_time():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/mix/market/funding-time"
        return _json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": {"nextFundingTime": "1700000000000"},
            }
        )

    adapter = _make_adapter(handler)
    next_time = await adapter.get_futures_funding_time("BTC/USDT")

    assert next_time.year == 2023


async def test_get_futures_open_interest():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/mix/market/open-interest"
        return _json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": {"openInterestList": [{"symbol": "BTCUSDT", "size": "1234.5"}]},
            }
        )

    adapter = _make_adapter(handler)
    oi = await adapter.get_futures_open_interest("BTC/USDT")

    assert oi == Decimal("1234.5")


async def test_get_futures_position_lever_tiers():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/mix/market/query-position-lever"
        return _json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [{"level": "1", "maxLever": "125"}],
            }
        )

    adapter = _make_adapter(handler)
    tiers = await adapter.get_futures_position_lever_tiers("BTC/USDT")

    assert tiers == [{"level": "1", "maxLever": "125"}]


async def test_set_futures_margin_sends_amount():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/mix/account/set-margin"
        body = json.loads(request.content)
        assert body["amount"] == "50"
        return _json_response({"code": "00000", "msg": "success", "requestTime": 1, "data": {}})

    adapter = _make_adapter(handler)
    await adapter.set_futures_margin("BTC/USDT", Decimal("50"))


async def test_get_futures_max_open_amount():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/mix/account/max-open"
        return _json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": {"maxOpenAvailable": "10"},
            }
        )

    adapter = _make_adapter(handler)
    amount = await adapter.get_futures_max_open_amount("BTC/USDT")

    assert amount == Decimal("10")


async def test_get_futures_account_bills_returns_raw_rows():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/mix/account/bill"
        return _json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [{"billId": "b-1", "amount": "10"}],
            }
        )

    adapter = _make_adapter(handler)
    bills = await adapter.get_futures_account_bills()

    assert bills == [{"billId": "b-1", "amount": "10"}]


async def test_get_futures_position_history_returns_raw_rows():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/mix/position/history-position"
        return _json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [{"symbol": "BTCUSDT", "netProfit": "12.5"}],
            }
        )

    adapter = _make_adapter(handler)
    history = await adapter.get_futures_position_history(symbol="BTC/USDT")

    assert history == [{"symbol": "BTCUSDT", "netProfit": "12.5"}]


async def test_cancel_all_futures_orders_returns_true_on_success():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/mix/order/cancel-all-orders"
        return _json_response({"code": "00000", "msg": "success", "requestTime": 1, "data": {}})

    adapter = _make_adapter(handler)
    assert await adapter.cancel_all_futures_orders(symbol="BTC/USDT") is True


async def test_place_futures_tpsl_order_sends_trigger_price():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/mix/order/place-tpsl-order"
        body = json.loads(request.content)
        assert body["triggerPrice"] == "85000"
        assert body["planType"] == "profit_plan"
        return _json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": {"orderId": "tp-1"},
            }
        )

    adapter = _make_adapter(handler)
    result = await adapter.place_futures_tpsl_order("BTC/USDT", "profit_plan", Decimal("85000"))

    assert result == {"orderId": "tp-1"}


async def test_place_futures_position_tpsl_requires_at_least_one_trigger():
    adapter = _make_adapter(lambda request: _json_response({"code": "00000", "data": {}}))
    with pytest.raises(ValueError):
        await adapter.place_futures_position_tpsl("BTC/USDT")


async def test_place_futures_position_tpsl_sends_both_triggers():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/mix/order/place-pos-tpsl"
        body = json.loads(request.content)
        assert body["stopSurplusTriggerPrice"] == "90000"
        assert body["stopLossTriggerPrice"] == "70000"
        return _json_response(
            {"code": "00000", "msg": "success", "requestTime": 1, "data": {"orderId": "pos-tp-1"}}
        )

    adapter = _make_adapter(handler)
    result = await adapter.place_futures_position_tpsl(
        "BTC/USDT", take_profit_trigger=Decimal("90000"), stop_loss_trigger=Decimal("70000")
    )

    assert result == {"orderId": "pos-tp-1"}


async def test_place_futures_plan_order_sends_trigger_price():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/mix/order/place-plan-order"
        body = json.loads(request.content)
        assert body["triggerPrice"] == "75000"
        return _json_response(
            {"code": "00000", "msg": "success", "requestTime": 1, "data": {"orderId": "plan-1"}}
        )

    adapter = _make_adapter(handler)
    result = await adapter.place_futures_plan_order(_order(), Decimal("75000"))

    assert result == {"orderId": "plan-1"}


async def test_cancel_futures_plan_order_returns_true_on_success():
    adapter = _make_adapter(
        lambda request: _json_response(
            {"code": "00000", "msg": "success", "requestTime": 1, "data": {}}
        )
    )
    assert await adapter.cancel_futures_plan_order("plan-1", symbol="BTC/USDT") is True


async def test_get_futures_current_plan_orders():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/mix/order/orders-plan-pending"
        return _json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": {"entrustedList": [{"orderId": "plan-1", "triggerPrice": "75000"}]},
            }
        )

    adapter = _make_adapter(handler)
    orders = await adapter.get_futures_current_plan_orders(symbol="BTC/USDT")

    assert orders == [{"orderId": "plan-1", "triggerPrice": "75000"}]


async def test_get_futures_current_plan_orders_handles_null_entrusted_list():
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(
            {"code": "00000", "msg": "success", "requestTime": 1, "data": {"entrustedList": None}}
        )

    adapter = _make_adapter(handler)
    assert await adapter.get_futures_current_plan_orders() == []
