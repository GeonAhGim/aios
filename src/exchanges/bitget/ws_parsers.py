"""02b_bitget_api_v2_full_spec_v1.md §6 / L4-19 — Bitget WebSocket 프레임 해석.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md#§2-E (`bitget/ws_parsers.py`)

실소켓 없이 검증 가능한 순수 함수만 둔다 — JSON 디코드된 dict를 받아
(1) 데이터 프레임은 도메인 모델로 파싱하고(parse_*_ws_message 6개),
(2) 제어 프레임(subscribe/login/error ack)은 `exchanges/common/ws_session.py`의
`AckResult`로 분류한다(`classify_bitget_ack`). 연결·하트비트·재연결은
`market_ws_connection.py`(→ 공용 `WsSession`) 참조.

2026-09-03 task-1032 — `market_data_mixin.py`(735줄)에서 순수 이동(동작 변경 0).
2026-09-06 task-1551(L4-19) — `market_ws_parsing.py`에서 이 이름으로 이동(스펙
§2-E 파일명 정합) + ack 분류·seq 추출 추가. 기존 테스트가 참조하는 모듈 경로
(`market_data_mixin`)는 그 파일에서 이 모듈의 이름들을 재-import해 유지한다.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from src.data.models.market_data import Candle, OrderBook, OrderBookLevel, Ticker
from src.data.models.trading import AccountBalance, Order, Position
from src.exchanges.bitget.futures_account_mixin import _row_to_position
from src.exchanges.bitget.parsers import parse_candles, parse_ticker
from src.exchanges.bitget.trading_mixin import _row_to_order
from src.exchanges.common.ws_session import NOT_ACK, AckResult, HeartbeatSpec

# 미검증(§10 U5): 30초 안에 평문 "ping"을 보내고 평문 "pong"을 받는다는
# 규약은 공식 문서 근거만 — 실키 검증(L4-30) 전까지 파라미터로만 고정.
BITGET_HEARTBEAT = HeartbeatSpec(interval_sec=30.0, ping_message="ping", pong_message="pong")

_LOGIN_OK_CODES = ("0", "00000", "None")


def _is_control_message(message: dict[str, Any]) -> bool:
    return message.get("event") in ("subscribe", "unsubscribe", "error", "login")


def classify_bitget_ack(message: dict[str, Any]) -> AckResult:
    """Bitget 제어 프레임 → `AckResult`(L4 §2-D "subscribe ack 실패 코드면 예외").

    - `event=subscribe|unsubscribe` → ack 성공.
    - `event=login` → code가 0/00000(또는 없음)이면 성공, 아니면 실패(task-105
      f3799ba의 성공/실패 구분을 그대로 옮김 — 이전엔 실패를 경고 로그로만 남겼다).
    - `event=error` → 실패(구독 거부·인증 오류는 재연결로 해결되지 않으므로
      세션이 예외로 표면화한다). 코드·메시지만 detail에 싣는다(raw payload 금지).
    - 그 외 → 데이터 프레임(`NOT_ACK`).
    """
    event = message.get("event")
    if event in ("subscribe", "unsubscribe"):
        label = "구독 성공" if event == "subscribe" else "구독 해제"
        return AckResult(is_ack=True, ok=True, detail=f"{label} arg={message.get('arg')}")
    if event == "login":
        code = str(message.get("code", "0"))
        return AckResult(
            is_ack=True,
            ok=code in _LOGIN_OK_CODES,
            detail=f"login code={code} msg={message.get('msg', '')}",
        )
    if event == "error":
        return AckResult(
            is_ack=True,
            ok=False,
            detail=f"error code={message.get('code')} msg={message.get('msg', '')} "
            f"arg={message.get('arg')}",
        )
    return NOT_ACK


def extract_bitget_seq(message: dict[str, Any]) -> int | None:
    """데이터 프레임의 `data[0].seq`를 int로 — 없으면 None(갭 검출 비활성).

    미검증: Bitget v2 스팟 채널 페이로드에 연결 단위 단조 증가 seq가 있는지
    문서로 확인하지 못했다. 전역 seq(심볼 간 공유)라면 매 메시지가 갭으로
    보여 REST 재동기화가 폭주하므로, 이 추출기는 `_run_ws_subscription`
    기본값이 아니라 호출부가 채널별로 검증 후 opt-in한다. 값이 있는데 정수가
    아니면 ValueError를 그대로 올린다(조용한 skip 금지).
    """
    rows = message.get("data")
    if not rows or not isinstance(rows, list) or not isinstance(rows[0], dict):
        return None
    raw_seq = rows[0].get("seq")
    if raw_seq is None:
        return None
    return int(raw_seq)


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
    return [parse_ticker(item) for item in message.get("data", [])]


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
    return parse_candles(rows, symbol, timeframe)


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
