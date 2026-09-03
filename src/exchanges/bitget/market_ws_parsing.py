"""02b_bitget_api_v2_full_spec_v1.md §6 — Bitget WebSocket 메시지 순수 파싱 함수군.

실소켓 없이도 검증 가능하도록 연결관리(`market_ws_connection.py`)와 분리한
순수 함수들 — JSON 디코드된 dict만 받는다.

2026-09-03 task-1032(PLT-40a 선행) — `market_data_mixin.py`(735줄, P6
line_cap 초과)에서 순수 이동(동작 변경 0). 기존 테스트가 참조하는 모듈
경로(`market_data_mixin`)는 그 파일에서 이 모듈의 이름들을 재-import해
그대로 유지한다.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from src.core.parser.candle_parser import parse_candles
from src.core.parser.ticker_parser import parse_ticker
from src.data.models.market_data import Candle, OrderBook, OrderBookLevel, Ticker
from src.data.models.trading import AccountBalance, Order, Position
from src.exchanges.bitget.futures_account_mixin import _row_to_position
from src.exchanges.bitget.trading_mixin import _row_to_order


def _is_control_message(message: dict[str, Any]) -> bool:
    return message.get("event") in ("subscribe", "error", "login")


def parse_order_ws_message(message: dict[str, Any]) -> list[Order]:
    """Private `orders` 채널 메시지 파싱 — REST orderInfo/unfilled-orders와
    행 형태를 공유한다고 가정(trading_mixin.py의 `_row_to_order()` 재사용,
    라이브 검증 필요)."""
    if _is_control_message(message):
        return []
    return [_row_to_order(row) for row in message.get("data", [])]


def parse_account_ws_message(message: dict[str, Any]) -> list[AccountBalance]:
    """Private `account` 채널 메시지 파싱 — REST get_balance()와 동일
    available/frozen/locked 필드 구조를 가정(라이브 검증 필요)."""
    if _is_control_message(message):
        return []
    balances = []
    for item in message.get("data", []):
        available = Decimal(item.get("available", "0"))
        frozen = Decimal(item.get("frozen", "0"))
        locked = Decimal(item.get("locked", "0"))
        balances.append(
            AccountBalance(
                exchange="bitget",
                asset=item.get("coin", "").upper(),
                total=available + frozen + locked,
                available=available,
                used_margin=frozen + locked,
            )
        )
    return balances


def parse_position_ws_message(message: dict[str, Any]) -> list[Position]:
    """Private `positions` 채널(선물 전용) 메시지 파싱 —
    futures_account_mixin.py의 `_row_to_position()` 재사용(라이브 검증
    필요)."""
    if _is_control_message(message):
        return []
    return [
        _row_to_position(item, item.get("symbol", item.get("instId", "")))
        for item in message.get("data", [])
    ]


def parse_ticker_ws_message(message: dict[str, Any]) -> list[Ticker]:
    """공개 ticker 채널 메시지 파싱 — REST parse_ticker()를 그대로 재사용
    (Bitget WS/REST가 동일 필드 이름을 씀, market_data_mixin.py 기존
    가정과 동일)."""
    if _is_control_message(message):
        return []
    return [parse_ticker(item, "bitget") for item in message.get("data", [])]


def parse_candle_ws_message(
    message: dict[str, Any], *, symbol: str, timeframe: str
) -> list[Candle]:
    """공개 candle 채널 메시지 파싱 — REST parse_candles()와 동일 행 형태
    ([ts, open, high, low, close, volume, ...])라고 가정(라이브 검증
    필요, 공통 관례)."""
    if _is_control_message(message):
        return []
    rows = message.get("data", [])
    if not rows:
        return []
    return parse_candles(rows, "bitget", symbol, timeframe)


def parse_orderbook_ws_message(message: dict[str, Any], *, symbol: str) -> OrderBook | None:
    """공개 books 채널 메시지 파싱 — snapshot/update 구분 없이 매 메시지를
    그 시점의 전체 호가창 스냅샷으로 취급한다(Phase 1 Draft — 델타 병합은
    필요해지면 별도 leaf, 지금은 매 메시지가 self-contained라고 가정)."""
    if _is_control_message(message):
        return None
    rows = message.get("data", [])
    if not rows:
        return None
    raw = rows[0]
    now = datetime.now(timezone.utc)
    bids = [OrderBookLevel(price=Decimal(p), quantity=Decimal(q)) for p, q in raw.get("bids", [])]
    asks = [OrderBookLevel(price=Decimal(p), quantity=Decimal(q)) for p, q in raw.get("asks", [])]
    return OrderBook(symbol=symbol, exchange="bitget", bids=bids, asks=asks, timestamp=now)
