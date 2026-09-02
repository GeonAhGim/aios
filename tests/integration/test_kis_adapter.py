"""6.11 — KISAdapter 통합 테스트.

실제 KIS 모의투자 앱키가 없는 상태(.env KIS_APP_KEY 비어있음)라
httpx.MockTransport로 조사한 실제 응답 형태를 재현해 검증한다. 실제
모의투자 계좌 왕복 테스트는 사용자가 앱키를 채운 뒤 별도로 수행해야 한다.
"""
import json
from decimal import Decimal

import httpx
import pytest

from src.core.exceptions import FatalExchangeError
from src.data.models.base import AssetClass
from src.exchanges.kis.adapter import KISAdapter

TOKEN_RESPONSE = {"access_token": "tok-1", "access_token_token_expired": "2099-01-01 00:00:00"}


def _make_adapter(handler) -> KISAdapter:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(
        base_url="https://openapivts.koreainvestment.com:29443", transport=transport
    )
    return KISAdapter("app", "secret", "12345678", "01", is_paper_trading=True, http_client=client)


def _route(request: httpx.Request, routes: dict) -> httpx.Response:
    if request.url.path == "/oauth2/tokenP":
        return httpx.Response(200, json=TOKEN_RESPONSE)
    handler = routes.get(request.url.path)
    assert handler is not None, f"no route for {request.url.path}"
    return handler(request)


async def test_get_ticker_combines_price_and_orderbook_endpoints():
    def price_handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["tr_id"] == "FHKST01010100"
        return httpx.Response(
            200,
            json={
                "rt_cd": "0",
                "msg1": "ok",
                "output": {"stck_prpr": "70000", "acml_vol": "12345"},
            },
        )

    def book_handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["tr_id"] == "FHKST01010200"
        return httpx.Response(
            200,
            json={
                "rt_cd": "0",
                "msg1": "ok",
                "output1": {"askp1": "70100", "bidp1": "69900"},
            },
        )

    routes = {
        "/uapi/domestic-stock/v1/quotations/inquire-price": price_handler,
        "/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn": book_handler,
    }
    adapter = _make_adapter(lambda request: _route(request, routes))

    ticker = await adapter.get_ticker("005930")

    assert ticker.price == Decimal("70000")
    assert ticker.bid == Decimal("69900")
    assert ticker.ask == Decimal("70100")
    assert ticker.exchange == "kis"


async def test_capabilities_declare_kr_equity_only():
    adapter = _make_adapter(lambda request: httpx.Response(200, json=TOKEN_RESPONSE))
    caps = adapter.get_capabilities()

    assert caps.supported_asset_classes == [AssetClass.KR_EQUITY]
    assert caps.supports_websocket is False
    assert caps.market_hours is not None
    assert caps.market_hours.timezone == "Asia/Seoul"


async def test_subscribe_ticker_stream_not_implemented():
    adapter = _make_adapter(lambda request: httpx.Response(200, json=TOKEN_RESPONSE))
    with pytest.raises(NotImplementedError):
        await adapter.subscribe_ticker_stream("005930", lambda t: None)


async def test_get_ohlcv_rejects_unsupported_timeframe_early():
    """02d 스펙 §2 — 1m 지원 추가 이후, 여전히 지원 안 하는 분봉(3m 등)은
    거부해야 한다(test_get_ohlcv_rejects_unsupported_timeframe와 중복
    방지를 위해 여기선 토큰 요청조차 가지 않는 조기 검증만 확인)."""
    adapter = _make_adapter(lambda request: httpx.Response(200, json=TOKEN_RESPONSE))
    with pytest.raises(ValueError):
        await adapter.get_ohlcv("005930", "3m")


async def test_get_balance_maps_holdings_and_cash():
    def balance_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "rt_cd": "0",
                "msg1": "ok",
                "output1": [{"pdno": "005930", "hldg_qty": "10", "ord_psbl_qty": "10"}],
                "output2": [{"dnca_tot_amt": "1000000"}],
            },
        )

    adapter = _make_adapter(
        lambda request: _route(
            request, {"/uapi/domestic-stock/v1/trading/inquire-balance": balance_handler}
        )
    )
    balances = await adapter.get_balance()

    assets = {b.asset: b.total for b in balances}
    assert assets["005930"] == Decimal("10")
    assert assets["KRW"] == Decimal("1000000")


async def test_get_positions_always_empty():
    adapter = _make_adapter(lambda request: httpx.Response(200, json=TOKEN_RESPONSE))
    assert await adapter.get_positions() == []


async def test_place_order_packs_composite_exchange_order_id():
    def order_handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["tr_id"] == "VTTC0012U"  # 모의투자 치환 확인
        body = json.loads(request.content)
        assert body["PDNO"] == "005930"
        return httpx.Response(
            200,
            json={
                "rt_cd": "0",
                "msg1": "ok",
                "output": {"KRX_FWDG_ORD_ORGNO": "1234", "ODNO": "999", "ORD_TMD": "091500"},
            },
        )

    from src.data.models.trading import Order, OrderSide, OrderType

    adapter = _make_adapter(
        lambda request: _route(
            request, {"/uapi/domestic-stock/v1/trading/order-cash": order_handler}
        )
    )
    order = Order(
        client_order_id="c-1",
        strategy_id="s-1",
        strategy_version="v1",
        symbol="005930",
        exchange="kis",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("10"),
        asset_class=AssetClass.KR_EQUITY,
    )

    result = await adapter.place_order(order)

    assert result.exchange_order_id == "1234:999"


async def test_place_order_missing_expected_field_raises_fatal_exchange_error():
    """docs/RED_TEAM_FINDINGS.md #18b 회귀 — 응답 형식이 바뀌어 예상 필드가
    없으면 설명 없는 KeyError 대신 FatalExchangeError로 통일돼야 한다."""

    def order_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"rt_cd": "0", "msg1": "ok", "output": {"ODNO": "999"}},  # KRX_FWDG_ORD_ORGNO 누락
        )

    from src.data.models.trading import Order, OrderSide, OrderType

    adapter = _make_adapter(
        lambda request: _route(
            request, {"/uapi/domestic-stock/v1/trading/order-cash": order_handler}
        )
    )
    order = Order(
        client_order_id="c-1",
        strategy_id="s-1",
        strategy_version="v1",
        symbol="005930",
        exchange="kis",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("10"),
        asset_class=AssetClass.KR_EQUITY,
    )

    with pytest.raises(FatalExchangeError):
        await adapter.place_order(order)


async def test_cancel_order_requires_composite_id_format():
    adapter = _make_adapter(lambda request: httpx.Response(200, json=TOKEN_RESPONSE))
    with pytest.raises(FatalExchangeError):
        await adapter.cancel_order("not-composite")


async def test_cancel_order_success():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["RVSE_CNCL_DVSN_CD"] == "02"
        assert body["KRX_FWDG_ORD_ORGNO"] == "1234"
        assert body["ORGN_ODNO"] == "999"
        return httpx.Response(200, json={"rt_cd": "0", "msg1": "ok", "output": {}})

    adapter = _make_adapter(
        lambda request: _route(request, {"/uapi/domestic-stock/v1/trading/order-rvsecncl": handler})
    )
    assert await adapter.cancel_order("1234:999") is True


async def test_health_check_true_on_success():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"rt_cd": "0", "msg1": "ok", "output1": [], "output2": []})

    adapter = _make_adapter(
        lambda request: _route(
            request, {"/uapi/domestic-stock/v1/trading/inquire-balance": handler}
        )
    )
    assert await adapter.health_check() is True


async def test_get_ohlcv_1m_uses_intraday_endpoint():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["tr_id"] == "FHKST03010200"
        return httpx.Response(
            200,
            json={
                "rt_cd": "0",
                "msg1": "ok",
                "output2": [
                    {
                        "stck_bsop_date": "20260902",
                        "stck_cntg_hour": "093000",
                        "stck_oprc": "70000",
                        "stck_hgpr": "70500",
                        "stck_lwpr": "69900",
                        "stck_prpr": "70200",
                        "cntg_vol": "1000",
                    }
                ],
            },
        )

    adapter = _make_adapter(
        lambda request: _route(
            request, {"/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice": handler}
        )
    )
    candles = await adapter.get_ohlcv("005930", "1m")

    assert candles[0].close == Decimal("70200")
    assert candles[0].timeframe == "1m"


async def test_get_ohlcv_rejects_unsupported_timeframe():
    adapter = _make_adapter(lambda request: httpx.Response(200, json=TOKEN_RESPONSE))
    with pytest.raises(ValueError):
        await adapter.get_ohlcv("005930", "5m")


async def test_is_market_holiday_true_when_closed():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["tr_id"] == "VTCA0903R"  # 모의투자 치환 확인
        return httpx.Response(
            200, json={"rt_cd": "0", "msg1": "ok", "output": [{"opnd_yn": "N"}]}
        )

    adapter = _make_adapter(
        lambda request: _route(request, {"/uapi/domestic-stock/v1/quotations/chk-holiday": handler})
    )
    assert await adapter.is_market_holiday("20260901") is True


async def test_get_buyable_amount_returns_raw_dict():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["tr_id"] == "VTTC8908R"  # 모의투자 치환 확인
        return httpx.Response(
            200,
            json={
                "rt_cd": "0",
                "msg1": "ok",
                "output": {"ord_psbl_cash": "1000000", "max_buy_qty": "14"},
            },
        )

    adapter = _make_adapter(
        lambda request: _route(
            request, {"/uapi/domestic-stock/v1/trading/inquire-psbl-order": handler}
        )
    )
    result = await adapter.get_buyable_amount("005930", Decimal("70000"))

    assert result == {"ord_psbl_cash": "1000000", "max_buy_qty": "14"}


async def test_get_sellable_quantity_parses_qty():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["tr_id"] == "VTTC8408R"  # 모의투자 치환 확인
        return httpx.Response(
            200, json={"rt_cd": "0", "msg1": "ok", "output": {"ord_psbl_qty": "10"}}
        )

    adapter = _make_adapter(
        lambda request: _route(
            request, {"/uapi/domestic-stock/v1/trading/inquire-psbl-sell": handler}
        )
    )
    qty = await adapter.get_sellable_quantity("005930")

    assert qty == Decimal("10")


async def test_get_cancelable_orders_returns_raw_rows():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["tr_id"] == "VTTC0084R"  # 모의투자 치환 확인
        return httpx.Response(
            200, json={"rt_cd": "0", "msg1": "ok", "output": [{"odno": "999"}]}
        )

    adapter = _make_adapter(
        lambda request: _route(
            request, {"/uapi/domestic-stock/v1/trading/inquire-psbl-rvsecncl": handler}
        )
    )
    orders = await adapter.get_cancelable_orders()

    assert orders == [{"odno": "999"}]


async def test_get_realized_pnl_returns_raw_rows():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["tr_id"] == "VTTC8494R"  # 모의투자 치환 확인
        return httpx.Response(
            200,
            json={
                "rt_cd": "0",
                "msg1": "ok",
                "output1": [{"pdno": "005930", "rlzt_pfls": "5000"}],
            },
        )

    adapter = _make_adapter(
        lambda request: _route(
            request, {"/uapi/domestic-stock/v1/trading/inquire-balance-rlz-pl": handler}
        )
    )
    pnl = await adapter.get_realized_pnl()

    assert pnl == [{"pdno": "005930", "rlzt_pfls": "5000"}]
