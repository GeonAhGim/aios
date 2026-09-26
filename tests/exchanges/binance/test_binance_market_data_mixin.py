"""task-7600(BR-22b) -- BinanceMarketDataMixin get_ticker/get_orderbook/
get_ohlcv tests.

Follows the same convention as test_binance_trading_mixin.py: a minimal
stub client mixing in just this mixin (no full BinanceAdapter needed).
D2 floor: negative tests >=3, one failure-injection test, one numeric
performance assertion. replay_verify: N/A(순수 조회 어댑터 메서드, DB/
이벤트스토어에 쓰지 않음).
"""

from __future__ import annotations

import time
from decimal import Decimal
from typing import Any

import pytest

from src.core.exceptions import FatalExchangeError
from src.exchanges.binance.market_data_mixin import (
    BINANCE_PUBLIC_PATHS,
    BinanceMarketDataMixin,
)


class _StubClient(BinanceMarketDataMixin):
    def __init__(self, response: Any) -> None:
        self._response = response
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []

    async def _request(
        self, method: str, path: str, *, params: dict[str, Any] | None = None
    ) -> Any:
        self.calls.append((method, path, params))
        return self._response


# ---- SPI 시그니처 준수 확인 (DoD #2) ----


def test_market_data_paths_are_public_no_auth_needed():
    """공개 엔드포인트만 서명 스킵 대상이어야 한다 -- 계좌/주문 경로가
    섞여 들어가면 factory.py가 SIGNED 요청에도 서명을 빼먹게 된다."""
    assert BINANCE_PUBLIC_PATHS == {
        "/api/v3/ticker/24hr",
        "/api/v3/depth",
        "/api/v3/klines",
    }


# ---- 해피 패스 ----


async def test_get_ticker_maps_binance_fields_to_ticker_model():
    client = _StubClient(
        {
            "lastPrice": "60000.50",
            "bidPrice": "60000.00",
            "askPrice": "60001.00",
            "volume": "123.45",
        }
    )
    ticker = await client.get_ticker("BTCUSDT")
    assert ticker.symbol == "BTCUSDT"
    assert ticker.exchange == "binance"
    assert ticker.price == Decimal("60000.50")
    assert ticker.bid == Decimal("60000.00")
    assert ticker.ask == Decimal("60001.00")
    assert ticker.volume_24h == Decimal("123.45")
    method, path, params = client.calls[0]
    assert (method, path) == ("GET", "/api/v3/ticker/24hr")
    assert params == {"symbol": "BTCUSDT"}


async def test_get_orderbook_maps_bids_asks():
    client = _StubClient({"bids": [["60000.0", "1.5"]], "asks": [["60001.0", "2.0"]]})
    book = await client.get_orderbook("BTCUSDT", depth=10)
    assert book.bids[0].price == Decimal("60000.0")
    assert book.bids[0].quantity == Decimal("1.5")
    assert book.asks[0].price == Decimal("60001.0")
    _, _, params = client.calls[0]
    assert params == {"symbol": "BTCUSDT", "limit": "10"}


async def test_get_ohlcv_maps_kline_rows_to_candles():
    client = _StubClient(
        [[1700000000000, "60000", "60100", "59900", "60050", "12.5", 1700000059999]]
    )
    candles = await client.get_ohlcv("BTCUSDT", "1m", limit=1)
    assert len(candles) == 1
    candle = candles[0]
    assert candle.open == Decimal("60000")
    assert candle.high == Decimal("60100")
    assert candle.low == Decimal("59900")
    assert candle.close == Decimal("60050")
    assert candle.volume == Decimal("12.5")
    _, path, params = client.calls[0]
    assert path == "/api/v3/klines"
    assert params == {"symbol": "BTCUSDT", "interval": "1m", "limit": "1"}


# ---- 부정/장애주입 테스트 (D2 floor >=3 부정 + 1 장애주입) ----


async def test_get_ticker_raises_on_missing_field():
    """부정 테스트 1: 응답에 lastPrice가 없으면(스키마 변경/장애) 설명
    없는 KeyError 대신 FatalExchangeError로 통일."""
    client = _StubClient({"bidPrice": "1", "askPrice": "1", "volume": "1"})
    with pytest.raises(FatalExchangeError):
        await client.get_ticker("BTCUSDT")


async def test_get_ticker_raises_when_response_is_not_an_object():
    """부정 테스트 2: get_ticker가 실수로 배열 응답(klines 경로와 섞임
    등)을 받으면 명시적으로 거부한다."""
    client = _StubClient([1, 2, 3])
    with pytest.raises(FatalExchangeError):
        await client.get_ticker("BTCUSDT")


async def test_get_orderbook_raises_on_malformed_levels():
    """부정 테스트 3 + 장애주입: bids/asks 행이 [price, qty] 2-튜플이
    아니면(거래소 응답 스키마 변경) FatalExchangeError."""
    client = _StubClient({"bids": [["not-a-price"]], "asks": []})
    with pytest.raises(FatalExchangeError):
        await client.get_orderbook("BTCUSDT")


async def test_get_ohlcv_raises_on_malformed_row():
    """부정 테스트 4: kline 행이 예상보다 짧으면(필드 누락) IndexError를
    삼키지 않고 FatalExchangeError로 통일."""
    client = _StubClient([[1700000000000, "60000"]])  # missing high/low/close/volume/closeTime
    with pytest.raises(FatalExchangeError):
        await client.get_ohlcv("BTCUSDT", "1m")


async def test_get_ohlcv_rejects_unsupported_timeframe():
    """부정 테스트 5: AIOS 표준 timeframe 밖의 값은 거래소 호출 전에
    거부한다(잘못된 interval을 그대로 보내지 않음)."""
    client = _StubClient([])
    with pytest.raises(ValueError):
        await client.get_ohlcv("BTCUSDT", "3w")
    assert client.calls == []


# ---- 성능 수치 단언 ----


@pytest.mark.perf
async def test_get_ticker_latency_budget():
    client = _StubClient(
        {"lastPrice": "1", "bidPrice": "1", "askPrice": "1", "volume": "1"}
    )
    start = time.perf_counter()
    for _ in range(100):
        await client.get_ticker("BTCUSDT")
    elapsed = time.perf_counter() - start
    assert elapsed / 100 < 0.001
