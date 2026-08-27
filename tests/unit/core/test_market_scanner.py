from datetime import datetime, timezone
from decimal import Decimal

import pytest

from src.core.scanner.market_scanner import ScanCriteria, scan_market
from src.data.models.market_data import Candle, Ticker


def _ticker(symbol: str, volume: str) -> Ticker:
    return Ticker(
        symbol=symbol,
        exchange="bitget",
        price=Decimal("100"),
        bid=Decimal("99.9"),
        ask=Decimal("100.1"),
        volume_24h=Decimal(volume),
        timestamp=datetime.now(timezone.utc),
        source_type="primary",
    )


def _candle(close: str) -> Candle:
    now = datetime.now(timezone.utc)
    return Candle(
        symbol="BTC/USDT",
        exchange="bitget",
        timeframe="1h",
        open=Decimal(close),
        high=Decimal(close),
        low=Decimal(close),
        close=Decimal(close),
        volume=Decimal("1"),
        open_time=now,
        close_time=now,
    )


async def test_scan_market_filters_by_min_volume():
    tickers = [_ticker("BTC/USDT", "1000"), _ticker("DOGE/USDT", "10")]

    async def fetch_tickers(exchange):
        return tickers

    result = await scan_market(
        ScanCriteria(min_volume_24h=Decimal("100"), exchanges=["bitget"]),
        fetch_tickers=fetch_tickers,
    )

    assert result == ["BTC/USDT"]


async def test_scan_market_no_criteria_returns_all():
    tickers = [_ticker("BTC/USDT", "1000"), _ticker("DOGE/USDT", "10")]

    async def fetch_tickers(exchange):
        return tickers

    result = await scan_market(ScanCriteria(exchanges=["bitget"]), fetch_tickers=fetch_tickers)

    assert result == ["BTC/USDT", "DOGE/USDT"]


async def test_scan_market_volatility_filter_requires_fetch_candles():
    async def fetch_tickers(exchange):
        return [_ticker("BTC/USDT", "1000")]

    with pytest.raises(ValueError):
        await scan_market(
            ScanCriteria(min_volatility=Decimal("0.01"), exchanges=["bitget"]),
            fetch_tickers=fetch_tickers,
        )


async def test_scan_market_volatility_filter_excludes_low_volatility_symbol():
    async def fetch_tickers(exchange):
        return [_ticker("STABLE/USDT", "1000")]

    async def fetch_candles(exchange, symbol):
        # 가격이 전혀 안 움직임 -> 변동성 0
        return [_candle("100"), _candle("100"), _candle("100")]

    result = await scan_market(
        ScanCriteria(min_volatility=Decimal("0.001"), exchanges=["bitget"]),
        fetch_tickers=fetch_tickers,
        fetch_candles=fetch_candles,
    )

    assert result == []


async def test_scan_market_multiple_exchanges():
    async def fetch_tickers(exchange):
        return [_ticker(f"BTC/{exchange}", "1000")]

    result = await scan_market(
        ScanCriteria(exchanges=["bitget", "kis"]), fetch_tickers=fetch_tickers
    )

    assert result == ["BTC/bitget", "BTC/kis"]
