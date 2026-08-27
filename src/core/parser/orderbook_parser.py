"""5.6 — Parser.parse_orderbook().

Spec: 03_core_modules_v1.1.md#§3.2

편차: candle_parser.py와 동일한 이유로 `symbol` 파라미터 추가 — Bitget v2
실제 응답(2026-08-28, `GET /api/v2/spot/market/orderbook` 라이브 확인)의
`data`에는 symbol이 없다(`{asks: [[price, size], ...], bids: [...], ts}`).
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from src.core.exceptions import FatalExchangeError
from src.data.models.market_data import OrderBook, OrderBookLevel


def _parse_levels(raw_levels: list[list[str]]) -> list[OrderBookLevel]:
    return [
        OrderBookLevel(price=Decimal(price), quantity=Decimal(quantity))
        for price, quantity in raw_levels
    ]


def parse_orderbook(raw: dict[str, Any], exchange: str, symbol: str) -> OrderBook:
    """FD-2.5 — Bitget 오더북 응답 → 표준 OrderBook 모델."""
    if exchange != "bitget":
        raise FatalExchangeError(f"parse_orderbook: 지원하지 않는 거래소 '{exchange}'")

    try:
        return OrderBook(
            symbol=symbol,
            exchange=exchange,
            bids=_parse_levels(raw["bids"]),
            asks=_parse_levels(raw["asks"]),
            timestamp=datetime.fromtimestamp(int(raw["ts"]) / 1000, tz=timezone.utc),
        )
    except KeyError as exc:
        raise FatalExchangeError(f"Bitget orderbook 응답에 예상 필드 없음: {exc}") from exc
