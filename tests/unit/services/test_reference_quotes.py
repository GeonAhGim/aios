"""참조 시세 어댑터 2종 단위테스트(R-47) — 실제 네트워크 호출 없음."""
from __future__ import annotations

from decimal import Decimal

import httpx

from src.services.safety.reference_quotes import (
    BinancePublicTickerReference,
    BitgetFuturesMarkPriceReference,
)


class _FakeBitgetAdapter:
    def __init__(self, *, price: str | None = None, raises: bool = False) -> None:
        self._price = price
        self._raises = raises

    async def get_futures_ticker(self, symbol: str):
        from datetime import datetime, timezone

        from src.data.models.market_data import Ticker

        if self._raises:
            raise RuntimeError("bitget futures ticker 조회 실패")
        return Ticker(
            symbol=symbol,
            exchange="bitget",
            price=Decimal(self._price),
            bid=Decimal(self._price),
            ask=Decimal(self._price),
            volume_24h=Decimal("10"),
            timestamp=datetime.now(timezone.utc),
            source_type="primary",
        )


async def test_bitget_futures_reference_returns_ticker_marked_as_reference():
    provider = BitgetFuturesMarkPriceReference(_FakeBitgetAdapter(price="100.5"))

    ticker = await provider.get_reference_ticker("BTC/USDT")

    assert ticker is not None
    assert ticker.price == Decimal("100.5")
    assert ticker.source_type == "reference"


async def test_bitget_futures_reference_returns_none_on_failure():
    provider = BitgetFuturesMarkPriceReference(_FakeBitgetAdapter(raises=True))

    ticker = await provider.get_reference_ticker("BTC/USDT")

    assert ticker is None


def _mock_transport(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.binance.com")


async def test_binance_reference_returns_ticker_on_success():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["symbol"] == "BTCUSDT"
        return httpx.Response(200, json={"symbol": "BTCUSDT", "price": "101.25"})

    provider = BinancePublicTickerReference(http_client=_mock_transport(handler))

    ticker = await provider.get_reference_ticker("BTC/USDT")

    assert ticker is not None
    assert ticker.price == Decimal("101.25")
    assert ticker.exchange == "binance"
    assert ticker.source_type == "reference"


async def test_binance_reference_returns_none_on_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(451, json={"msg": "restricted"})

    provider = BinancePublicTickerReference(http_client=_mock_transport(handler))

    ticker = await provider.get_reference_ticker("BTC/USDT")

    assert ticker is None


async def test_binance_reference_returns_none_on_malformed_body():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"symbol": "BTCUSDT"})  # price 필드 없음

    provider = BinancePublicTickerReference(http_client=_mock_transport(handler))

    ticker = await provider.get_reference_ticker("BTC/USDT")

    assert ticker is None


async def test_binance_reference_returns_none_on_timeout():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out", request=request)

    provider = BinancePublicTickerReference(http_client=_mock_transport(handler))

    ticker = await provider.get_reference_ticker("BTC/USDT")

    assert ticker is None
