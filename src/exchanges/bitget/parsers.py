"""5.4/5.5/5.6 — Bitget 전용 raw 응답 파서(ticker/orderbook/candle).

Spec: 03_core_modules_v1.1.md#§3.2, docs/design/ADR-2026-09-06-I §D5

BR-9(task-1787) — 이 세 함수는 원래 `src/core/parser/*`(거래소 공용을
표방하는 위치)에 있었고 `exchange` 인자로 `if exchange != "bitget": raise`
가드를 뒀다. 실제로는 필드명(`lastPr`/`bidPr`), 캔들 배열 순서, 오더북
`{asks, bids, ts}` 구조 전부 Bitget v2 wire format 그대로라 다른 거래소가
이 함수를 공유할 수 없었다(KIS/NH는 각자 자기 mixin 안에서 직접 파싱).
"core"를 자처하면서 실제로는 문자열 하나로 게이트되는 단일 거래소
구현이었던 것 — ADR D5(비트겟 전용 개념이 상위 계층에 새면 안 됨)를
어겼다. 이 파일로 옮기고 `exchange` 매개변수 자체를 없애 새는 지점을
지운다(이 모듈은 이미 `src/exchanges/bitget/` 소속이므로 "bitget"임을
증명할 문자열 비교가 애초에 필요 없다).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from src.core.exceptions import FatalExchangeError
from src.data.models.market_data import Candle, OrderBook, OrderBookLevel, Ticker

_EXCHANGE = "bitget"

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

# Phase 1 스콥(06번 §6.1) — Bitget은 USDT 마켓만 대상.
_KNOWN_QUOTE_SUFFIXES = ("USDT",)


def _to_canonical_symbol(exchange_symbol: str) -> str:
    """"BTCUSDT" -> "BTC/USDT". Phase 1은 USDT 마켓만 지원(06번 §6.1)."""
    for quote in _KNOWN_QUOTE_SUFFIXES:
        if exchange_symbol.endswith(quote):
            base = exchange_symbol[: -len(quote)]
            return f"{base}/{quote}"
    raise FatalExchangeError(f"인식할 수 없는 심볼 형식(지원 마켓 아님): {exchange_symbol}")


def _parse_ts_ms(raw_ts: str) -> datetime:
    return datetime.fromtimestamp(int(raw_ts) / 1000, tz=timezone.utc)


def parse_ticker(raw: dict[str, Any], *, source_type: str = "primary") -> Ticker:
    """Bitget v2 실제 응답(2026-08-28, `GET /api/v2/spot/market/tickers`
    라이브 확인) 필드명 그대로 매핑: symbol/lastPr/bidPr/askPr/baseVolume/ts.

    `raw`는 Adapter가 `{code, msg, data: [...]}` 봉투를 이미 벗기고 넘긴
    단일 ticker dict를 전제한다.
    """
    try:
        return Ticker(
            symbol=_to_canonical_symbol(raw["symbol"]),
            exchange=_EXCHANGE,
            price=Decimal(raw["lastPr"]),
            bid=Decimal(raw["bidPr"]),
            ask=Decimal(raw["askPr"]),
            volume_24h=Decimal(raw["baseVolume"]),
            timestamp=_parse_ts_ms(raw["ts"]),
            source_type=source_type,
        )
    except KeyError as exc:
        # FD-2.5 예외상황: 예상 필드 누락 시 조용히 기본값을 채우지 않고 즉시 실패시킨다
        # (거래소 API 스펙 변경 가능성을 바로 드러내기 위함).
        raise FatalExchangeError(f"Bitget ticker 응답에 예상 필드 없음: {exc}") from exc


def _parse_levels(raw_levels: list[list[str]]) -> list[OrderBookLevel]:
    return [
        OrderBookLevel(price=Decimal(price), quantity=Decimal(quantity))
        for price, quantity in raw_levels
    ]


def parse_orderbook(raw: dict[str, Any], symbol: str) -> OrderBook:
    """Bitget v2 실제 응답(2026-08-28, `GET /api/v2/spot/market/orderbook`
    라이브 확인)의 `data`에는 symbol이 없다(`{asks: [[price, size], ...],
    bids: [...], ts}`) — 호출자가 이미 아는 symbol을 그대로 받는다."""
    try:
        return OrderBook(
            symbol=symbol,
            exchange=_EXCHANGE,
            bids=_parse_levels(raw["bids"]),
            asks=_parse_levels(raw["asks"]),
            timestamp=datetime.fromtimestamp(int(raw["ts"]) / 1000, tz=timezone.utc),
        )
    except KeyError as exc:
        raise FatalExchangeError(f"Bitget orderbook 응답에 예상 필드 없음: {exc}") from exc


def parse_candles(
    raw: list[list[Any]], symbol: str, timeframe: str
) -> list[Candle]:
    """Bitget v2 실제 응답(2026-08-28, `GET /api/v2/spot/market/candles`
    라이브 확인)은 포지셔널 배열의 리스트다 — `[[ts, open, high, low,
    close, baseVolume, quoteVolume, usdtVolume], ...]`(dict 아님). 각 행에
    symbol이 없어 호출자가 이미 아는 symbol을 그대로 받는다."""
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
                exchange=_EXCHANGE,
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
