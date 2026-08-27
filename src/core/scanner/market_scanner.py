"""5.9 — Scanner.ScanCriteria + scan_market().

Spec: 03_core_modules_v1.1.md#§3.4

6.8 원칙 — 투자전략 자체와 분리. 조건에 맞는 종목을 찾을 뿐 매매 판단은
하지 않는다.

편차: ExchangeAdapter(작업트리 6번)가 아직 없어, 실제 시세 조회를 콜백으로
주입받는 순수 오케스트레이션 함수로 설계했다(recovery.py와 동일 패턴).
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
    """종가 기준 수익률의 표준편차(무차원 비율) — 최소 2개 캔들 필요."""
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
    """criteria.exchanges 각각에서 fetch_tickers(exchange)로 후보를 조회하고
    min_volume_24h로 1차 필터링한다. min_volatility가 설정되면
    fetch_candles(exchange, symbol)로 최근 캔들을 받아 실현 변동성을 계산해
    2차 필터링한다(호출 비용이 크므로 필요할 때만 호출)."""
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
