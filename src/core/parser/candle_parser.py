"""5.5 — Parser.parse_candles().

Spec: 03_core_modules_v1.1.md#§3.2

편차: 03번 문서 원 시그니처는 `raw: list[dict]`를 가정했으나, Bitget v2
실제 응답(2026-08-28, `GET /api/v2/spot/market/candles` 라이브 확인)은
포지셔널 배열의 리스트다 — `[[ts, open, high, low, close, baseVolume,
quoteVolume, usdtVolume], ...]`(dict 아님). 또한 각 행에 symbol이 없어
`symbol` 파라미터를 추가했다(호출자가 이미 알고 있는 값 — 이 심볼로
캔들을 요청했으므로).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from src.core.exceptions import FatalExchangeError
from src.data.models.market_data import Candle

# AIOS 표준 timeframe → 캔들 길이. Adapter가 이 표준 문자열을 거래소별
# granularity 파라미터(예: Bitget "1min")로 변환해 요청한다(Adapter 책임).
_TIMEFRAME_DURATIONS: dict[str, timedelta] = {
    "1m": timedelta(minutes=1),
    "5m": timedelta(minutes=5),
    "15m": timedelta(minutes=15),
    "30m": timedelta(minutes=30),
    "1h": timedelta(hours=1),
    "4h": timedelta(hours=4),
    "1d": timedelta(days=1),
}


def parse_candles(
    raw: list[list[Any]], exchange: str, symbol: str, timeframe: str
) -> list[Candle]:
    """FD-2.5 — Bitget 캔들 배열 → 표준 Candle 모델 리스트."""
    if exchange != "bitget":
        raise FatalExchangeError(f"parse_candles: 지원하지 않는 거래소 '{exchange}'")

    duration = _TIMEFRAME_DURATIONS.get(timeframe)
    if duration is None:
        raise FatalExchangeError(f"parse_candles: 알 수 없는 timeframe '{timeframe}'")

    candles = []
    for row in raw:
        try:
            ts_ms, open_, high, low, close, volume = row[0], row[1], row[2], row[3], row[4], row[5]
        except IndexError as exc:
            raise FatalExchangeError(f"Bitget candle 행의 필드 수가 예상과 다름: {row}") from exc

        open_time = datetime.fromtimestamp(int(ts_ms) / 1000, tz=timezone.utc)
        candles.append(
            Candle(
                symbol=symbol,
                exchange=exchange,
                timeframe=timeframe,
                open=Decimal(open_),
                high=Decimal(high),
                low=Decimal(low),
                close=Decimal(close),
                volume=Decimal(volume),
                open_time=open_time,
                close_time=open_time + duration,
            )
        )
    return candles
