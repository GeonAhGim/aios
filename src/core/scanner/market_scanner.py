"""5.9 — Scanner.ScanCriteria + scan_market().

Spec: 03_core_modules_v1.1.md#§3.4

Principle 6.8 — kept separate from the trading strategy itself. This only
finds symbols matching criteria; it never makes a trade decision.

Deviation: ExchangeAdapter (worktree 6) does not exist yet, so this is
designed as a pure orchestration function that receives live quote lookups
via injected callbacks (same pattern as recovery.py).
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from decimal import Decimal

from pydantic import BaseModel, Field

from src.data.models.market_data import Candle, Ticker

FetchTickers = Callable[[str], Awaitable[list[Ticker]]]
FetchCandles = Callable[[str, str], Awaitable[list[Candle]]]


class ScanCriteria(BaseModel):
    min_volume_24h: Decimal | None = None
    min_volatility: Decimal | None = None
    exchanges: list[str] = Field(default_factory=list)


def _realized_volatility(candles: list[Candle]) -> Decimal:
    """Standard deviation of close-to-close returns (dimensionless ratio) — needs >=2 candles."""
    if len(candles) < 2:
        return Decimal("0")
    closes = [c.close for c in candles]
    returns = [(closes[i] - closes[i - 1]) / closes[i - 1] for i in range(1, len(closes))]
    mean: Decimal = sum(returns, Decimal("0")) / len(returns)
    variance: Decimal = sum(((r - mean) ** 2 for r in returns), Decimal("0")) / len(returns)
    result: Decimal = variance.sqrt()
    return result


async def scan_market(
    criteria: ScanCriteria,
    *,
    fetch_tickers: FetchTickers,
    fetch_candles: FetchCandles | None = None,
) -> list[str]:
    """Fetches candidates from fetch_tickers(exchange) for each of criteria.exchanges
    and applies a first-pass filter on min_volume_24h. If min_volatility is set,
    fetches recent candles via fetch_candles(exchange, symbol) to compute realized
    volatility for a second-pass filter (called only when needed, since it's costly)."""
    matched: list[str] = []
    for exchange in criteria.exchanges:
        tickers = await fetch_tickers(exchange)
        for ticker in tickers:
            if criteria.min_volume_24h is not None and ticker.volume_24h < criteria.min_volume_24h:
                continue
            if criteria.min_volatility is not None:
                if fetch_candles is None:
                    raise ValueError("min_volatility 조건을 쓰려면 fetch_candles가 필요합니다.")
                candles = await fetch_candles(exchange, ticker.symbol)
                if _realized_volatility(candles) < criteria.min_volatility:
                    continue
            matched.append(ticker.symbol)
    return matched
