"""NHAdapter 통합 테스트 — market data(시세/호가/캔들) 및 account(잔고/포지션).

RATCHET-split(task-10196)로 test_nh_adapter.py에서 분리했다. 공유 헬퍼는
`_nh_adapter_helpers.py` 참조.
"""

import json
from decimal import Decimal

import httpx
import pytest

from src.core.exceptions import FatalExchangeError
from src.data.models.base import AssetClass
from tests.integration._nh_adapter_helpers import TOKEN_RESPONSE, _make_adapter, _route, _success

# ---------- market data ----------


async def test_get_ticker_parses_output():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/krstock/quote/v1/currentPrice"
        body = json.loads(request.content)
        assert body["iem_cd"] == "005930"
        return httpx.Response(
            200, json=_success({"stck_prpr": "70000", "bidp": "69900", "askp": "70100"})
        )

    adapter = _make_adapter(
        lambda request: _route(request, {"/krstock/quote/v1/currentPrice": handler})
    )
    ticker = await adapter.get_ticker("005930")

    assert ticker.price == Decimal("70000")
    assert ticker.bid == Decimal("69900")


async def test_get_ticker_raises_fatal_when_field_missing():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_success({"unexpected_field": "1"}))

    adapter = _make_adapter(
        lambda request: _route(request, {"/krstock/quote/v1/currentPrice": handler})
    )
    with pytest.raises(FatalExchangeError):
        await adapter.get_ticker("005930")


def _depth_output(**extra) -> dict:
    output = {"stck_prpr": "70000"}
    output.update(extra)
    return output


async def test_get_orderbook_parses_full_depth():
    output = _depth_output(
        askp1="70100",
        askp2="70200",
        askp_rsqn1="10",
        askp_rsqn2="20",
        bidp1="69900",
        bidp2="69800",
        bidp_rsqn1="30",
        bidp_rsqn2="40",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_success(output))

    adapter = _make_adapter(
        lambda request: _route(request, {"/krstock/quote/v1/currentPrice": handler})
    )
    book = await adapter.get_orderbook("005930")

    assert len(book.bids) == 2
    assert len(book.asks) == 2
    assert book.bids[0].price == Decimal("69900")
    assert book.bids[0].quantity == Decimal("30")
    assert book.asks[0].price == Decimal("70100")
    assert book.asks[0].quantity == Decimal("10")


async def test_get_orderbook_raises_fatal_when_no_quote_fields():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_success({"stck_prpr": "70000"}))

    adapter = _make_adapter(
        lambda request: _route(request, {"/krstock/quote/v1/currentPrice": handler})
    )
    with pytest.raises(FatalExchangeError):
        await adapter.get_orderbook("005930")


async def test_get_orderbook_raises_fatal_when_depth_quantity_missing():
    """askp1은 있는데 짝이 되는 잔량 askp_rsqn1이 없는 경우(비대칭 필드
    누락) — 조용히 quantity=0으로 채우지 않고 실패한다."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=_success(_depth_output(askp1="70100", bidp1="69900", bidp_rsqn1="30"))
        )

    adapter = _make_adapter(
        lambda request: _route(request, {"/krstock/quote/v1/currentPrice": handler})
    )
    with pytest.raises(FatalExchangeError):
        await adapter.get_orderbook("005930")


async def test_get_ohlcv_returns_daily_candles():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/krstock/quote/v1/currentDaily"
        body = json.loads(request.content)
        assert body["iem_cd"] == "005930"
        return httpx.Response(
            200,
            json=_success(
                [
                    {
                        "bsop_date": "20260924",
                        "stck_oppr": "50000",
                        "stck_hgpr": "51000",
                        "stck_lwpr": "49500",
                        "stck_clpr": "50500",
                        "acml_vol": "1000000",
                    }
                ]
            ),
        )

    adapter = _make_adapter(
        lambda request: _route(request, {"/krstock/quote/v1/currentDaily": handler})
    )
    candles = await adapter.get_ohlcv("005930", "1d")

    assert len(candles) == 1
    assert candles[0].close == Decimal("50500")


async def test_get_ohlcv_rejects_unsupported_timeframe():
    adapter = _make_adapter(lambda request: httpx.Response(200, json=TOKEN_RESPONSE))
    with pytest.raises(ValueError, match="only daily"):
        await adapter.get_ohlcv("005930", "1h")


async def test_capabilities_declare_websocket_supported():
    adapter = _make_adapter(lambda request: httpx.Response(200, json=TOKEN_RESPONSE))
    caps = adapter.get_capabilities()

    assert caps.supported_asset_classes == [AssetClass.KR_EQUITY]
    assert caps.supports_websocket is True  # task-2615 -- mc 채널 필드 스키마 확인됨
    assert caps.market_hours is not None


# ---------- account ----------


async def test_get_balance_maps_holdings_and_cash():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/krstock/inquiry/v1/balance"
        body = json.loads(request.content)
        assert body["act_no"] == "1234567890"
        return httpx.Response(
            200,
            json=_success(
                {"dca": "1000000"},
                Output_1=[{"iem_cd": "005930", "itg_bnc_qty": "10", "rsdl_qty": "8"}],
            ),
        )

    adapter = _make_adapter(
        lambda request: _route(request, {"/krstock/inquiry/v1/balance": handler})
    )
    balances = await adapter.get_balance()

    by_asset = {b.asset: b for b in balances}
    assert by_asset["005930"].total == Decimal("10")
    assert by_asset["005930"].available == Decimal("8")
    assert by_asset["KRW"].total == Decimal("1000000")


async def test_get_balance_raises_fatal_when_holding_field_missing():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_success({"dca": "1000000"}, Output_1=[{"iem_cd": "005930"}]),
        )

    adapter = _make_adapter(
        lambda request: _route(request, {"/krstock/inquiry/v1/balance": handler})
    )
    with pytest.raises(FatalExchangeError):
        await adapter.get_balance()


async def test_get_positions_always_empty():
    adapter = _make_adapter(lambda request: httpx.Response(200, json=TOKEN_RESPONSE))
    assert await adapter.get_positions() == []
