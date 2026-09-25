"""6.11 — BitgetAdapter market-data/account read-path integration tests.

Split out of the former single `tests/integration/test_bitget_adapter.py`
(788 lines, task-4225) — this file covers ticker/balance/positions/candles/
symbol-info/public-trades/account/health-check, i.e. read-only endpoints.
Real Bitget Demo account API keys aren't available (.env BITGET_API_KEY is
empty), so httpx.MockTransport replays captured Bitget response shapes.
"""

from decimal import Decimal

import httpx

from src.data.models.base import AssetClass


async def test_get_ticker_parses_real_response_shape(make_adapter, json_response):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/spot/market/tickers"
        assert request.headers["ACCESS-KEY"] == "key"
        assert request.headers["paptrading"] == "1"
        return json_response(
            {
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
        )

    adapter = make_adapter(handler)
    ticker = await adapter.get_ticker("BTC/USDT")

    assert ticker.symbol == "BTC/USDT"
    assert ticker.price == Decimal("80663.08")


async def test_capabilities_declare_crypto_only(make_adapter, json_response, real_ticker_envelope):
    adapter = make_adapter(lambda request: json_response(real_ticker_envelope))
    caps = adapter.get_capabilities()

    assert caps.supported_asset_classes == [AssetClass.CRYPTO]
    assert caps.supports_futures is False


async def test_get_balance_maps_coin_amounts(make_adapter, json_response):
    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [
                    {"coin": "usdt", "available": "100", "frozen": "5", "locked": "0"},
                ],
            }
        )

    adapter = make_adapter(handler)
    balances = await adapter.get_balance()

    assert balances[0].asset == "USDT"
    assert balances[0].total == Decimal("105")
    assert balances[0].available == Decimal("100")


async def test_get_positions_empty_when_no_balances(make_adapter, json_response):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/spot/account/assets"
        return json_response({"code": "00000", "msg": "success", "requestTime": 1, "data": []})

    adapter = make_adapter(handler)
    assert await adapter.get_positions() == []


async def test_get_positions_synthesizes_from_balance_and_ticker(
    make_adapter, json_response, real_ticker_envelope
):
    """FULL_AUDIT §2-B ④ 회귀 — 이전엔 get_positions()가 항상 빈 리스트를
    반환했다(HTTP 호출조차 없었음). 이제 get_balance()의 코인별 보유량을
    현재가와 합쳐 Position으로 합성하고, quote 통화(USDT)는 현금이라
    제외한다."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/spot/account/assets":
            return json_response(
                {
                    "code": "00000",
                    "msg": "success",
                    "requestTime": 1,
                    "data": [
                        {"coin": "btc", "available": "0.5", "frozen": "0", "locked": "0"},
                        {"coin": "usdt", "available": "1000", "frozen": "0", "locked": "0"},
                    ],
                }
            )
        assert request.url.path == "/api/v2/spot/market/tickers"
        return json_response(real_ticker_envelope)

    adapter = make_adapter(handler)
    positions = await adapter.get_positions()

    assert len(positions) == 1
    assert positions[0].symbol == "BTC/USDT"
    assert positions[0].quantity == Decimal("0.5")
    assert positions[0].current_price.amount == Decimal("80663.08")
    assert positions[0].unrealized_pnl.amount == Decimal("0")


async def test_get_positions_skips_coin_when_ticker_fails(make_adapter, json_response):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/spot/account/assets":
            return json_response(
                {
                    "code": "00000",
                    "msg": "success",
                    "requestTime": 1,
                    "data": [{"coin": "xyz", "available": "1", "frozen": "0", "locked": "0"}],
                }
            )
        assert request.url.path == "/api/v2/spot/market/tickers"
        return json_response({"code": "99999", "msg": "not found", "requestTime": 1, "data": {}})

    adapter = make_adapter(handler)
    assert await adapter.get_positions() == []


async def test_health_check_returns_false_on_error(make_adapter, json_response):
    def handler(request: httpx.Request) -> httpx.Response:
        return json_response({"code": "99999", "msg": "error", "data": {}})

    adapter = make_adapter(handler)
    assert await adapter.health_check() is False


async def test_health_check_returns_true_on_success(make_adapter, json_response):
    def handler(request: httpx.Request) -> httpx.Response:
        return json_response({"code": "00000", "msg": "success", "requestTime": 1, "data": []})

    adapter = make_adapter(handler)
    assert await adapter.health_check() is True


async def test_get_history_candles_parses_rows(make_adapter, json_response):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/spot/market/history-candles"
        assert request.url.params["endTime"] == "1700000000000"
        return json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [["1700000000000", "80000", "81000", "79500", "80500", "12.3"]],
            }
        )

    adapter = make_adapter(handler)
    candles = await adapter.get_history_candles("BTC/USDT", "1h", end_time="1700000000000")

    assert candles[0].close == Decimal("80500")
    assert candles[0].timeframe == "1h"


async def test_get_symbol_info_derives_tick_and_lot_from_precision(make_adapter, json_response):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/spot/public/symbols"
        return json_response(
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

    adapter = make_adapter(handler)
    symbols = await adapter.get_symbol_info()

    assert symbols[0].symbol == "BTC/USDT"
    assert symbols[0].tick_size == Decimal("0.01")
    assert symbols[0].lot_size == Decimal("0.0001")
    assert symbols[0].status == "online"


async def test_get_public_trades_parses_fills(make_adapter, json_response):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/spot/market/fills"
        return json_response(
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

    adapter = make_adapter(handler)
    trades = await adapter.get_public_trades("BTC/USDT")

    assert trades[0].trade_id == "t-1"
    assert trades[0].price == Decimal("80000")
    assert trades[0].side == "buy"


async def test_get_account_info_returns_raw_dict(make_adapter, json_response):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/spot/account/info"
        return json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": {"userId": "u-1", "authorities": ["spot_trade"]},
            }
        )

    adapter = make_adapter(handler)
    info = await adapter.get_account_info()

    assert info == {"userId": "u-1", "authorities": ["spot_trade"]}


async def test_get_account_bills_returns_raw_rows(make_adapter, json_response):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/spot/account/bills"
        assert request.url.params["coin"] == "USDT"
        return json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [{"billId": "b-1", "coin": "USDT", "amount": "10"}],
            }
        )

    adapter = make_adapter(handler)
    bills = await adapter.get_account_bills("USDT")

    assert bills == [{"billId": "b-1", "coin": "USDT", "amount": "10"}]


async def test_get_server_time_parses_timestamp(make_adapter, json_response):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/public/time"
        return json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": {"serverTime": "1700000000000"},
            }
        )

    adapter = make_adapter(handler)
    server_time = await adapter.get_server_time()

    assert server_time.year == 2023


async def test_get_trade_rate_returns_raw_dict(make_adapter, json_response):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/common/trade-rate"
        assert request.url.params["symbol"] == "BTCUSDT"
        return json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": {"makerFeeRate": "0.001", "takerFeeRate": "0.001"},
            }
        )

    adapter = make_adapter(handler)
    rate = await adapter.get_trade_rate("BTC/USDT")

    assert rate == {"makerFeeRate": "0.001", "takerFeeRate": "0.001"}


async def test_get_auction_returns_raw_dict(make_adapter, json_response):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/spot/market/auction"
        assert request.url.params["symbol"] == "BTCUSDT"
        return json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": {
                    "symbol": "BTCUSDT",
                    "auctionPrice": "80000",
                    "auctionTime": "1700000000000",
                },
            }
        )

    adapter = make_adapter(handler)
    auction = await adapter.get_auction("BTC/USDT")

    assert auction["symbol"] == "BTCUSDT"
    assert auction["auctionPrice"] == "80000"


async def test_get_merge_depth_returns_raw_dict(make_adapter, json_response):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/spot/market/merge-depth"
        assert request.url.params["symbol"] == "BTCUSDT"
        assert request.url.params["limit"] == "20"
        return json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": {
                    "asks": [["80000", "1.0"]],
                    "bids": [["79999", "1.0"]],
                    "ts": "1700000000000",
                },
            }
        )

    adapter = make_adapter(handler)
    depth = await adapter.get_merge_depth("BTC/USDT", limit=20)

    assert "asks" in depth
    assert "bids" in depth
    assert depth["asks"][0][0] == "80000"


async def test_get_vip_fee_rate_returns_raw_dict(make_adapter, json_response):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/spot/market/vip-fee-rate"
        return json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": {"level": "vip0", "makerFeeRate": "0.001", "takerFeeRate": "0.001"},
            }
        )

    adapter = make_adapter(handler)
    fee_rate = await adapter.get_vip_fee_rate()

    assert fee_rate["level"] == "vip0"
    assert fee_rate["makerFeeRate"] == "0.001"


async def test_get_coins_returns_list(make_adapter, json_response):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/spot/public/coins"
        return json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [
                    {"coin": "BTC", "name": "Bitcoin", "chains": ["BTC", "ETH"]},
                    {"coin": "USDT", "name": "Tether", "chains": ["TRX", "ETH"]},
                ],
            }
        )

    adapter = make_adapter(handler)
    coins = await adapter.get_coins()

    assert len(coins) == 2
    assert coins[0]["coin"] == "BTC"
    assert coins[1]["coin"] == "USDT"


async def test_get_deposit_address_returns_raw_dict(make_adapter, json_response):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/spot/wallet/deposit-address"
        assert request.url.params["coin"] == "BTC"
        return json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": {"coin": "BTC", "address": "1A1z7agoat", "chain": "BTC"},
            }
        )

    adapter = make_adapter(handler)
    addr = await adapter.get_deposit_address("BTC")

    assert addr["address"] == "1A1z7agoat"
    assert addr["coin"] == "BTC"


async def test_get_deposit_records_returns_list(make_adapter, json_response):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/spot/wallet/deposit-records"
        assert request.url.params["coin"] == "BTC"
        assert request.url.params["limit"] == "50"
        return json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [
                    {
                        "depositId": "d-1",
                        "coin": "BTC",
                        "amount": "0.5",
                        "status": "success",
                        "cTime": "1700000000000",
                    }
                ],
            }
        )

    adapter = make_adapter(handler)
    records = await adapter.get_deposit_records("BTC", limit=50)

    assert len(records) == 1
    assert records[0]["depositId"] == "d-1"
    assert records[0]["status"] == "success"


async def test_get_withdrawal_records_returns_list(make_adapter, json_response):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/spot/wallet/withdrawal-records"
        assert request.url.params["coin"] == "USDT"
        return json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": [
                    {
                        "withdrawalId": "w-1",
                        "coin": "USDT",
                        "amount": "100",
                        "status": "success",
                        "cTime": "1700000000000",
                    }
                ],
            }
        )

    adapter = make_adapter(handler)
    records = await adapter.get_withdrawal_records("USDT")

    assert len(records) == 1
    assert records[0]["withdrawalId"] == "w-1"
    assert records[0]["status"] == "success"
