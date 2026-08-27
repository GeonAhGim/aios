"""5.4 — Parser.parse_ticker().

Spec: 03_core_modules_v1.1.md#§3.2

Bitget v2 실제 응답(2026-08-28, `GET /api/v2/spot/market/tickers` 라이브 확인)
필드명 그대로 매핑: symbol/lastPr/bidPr/askPr/baseVolume/ts.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from src.core.exceptions import FatalExchangeError
from src.data.models.market_data import Ticker

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


def parse_ticker(raw: dict[str, Any], exchange: str, *, source_type: str = "primary") -> Ticker:
    """FD-2.5 — 거래소별 Raw ticker(단일 항목) → 표준 Ticker 모델.

    `raw`는 Adapter가 `{code, msg, data: [...]}` 봉투를 이미 벗기고 넘긴
    단일 ticker dict를 전제한다(03번 문서 원 시그니처와 동일).
    """
    if exchange != "bitget":
        raise FatalExchangeError(f"parse_ticker: 지원하지 않는 거래소 '{exchange}'")

    try:
        return Ticker(
            symbol=_to_canonical_symbol(raw["symbol"]),
            exchange=exchange,
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
