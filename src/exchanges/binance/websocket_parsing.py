"""BR-22e(task-21977) — Binance WebSocket 메시지 파서.

Spec: docs/exchanges/ADDING_AN_EXCHANGE.md 1단계,
docs/specs/L4_execution_oms_and_exchange_v1.0.md#L4-19.

Binance public WS 채널(ticker/trade, depth/orderbook)의 JSON 페이로드를
도메인 모델(Ticker, OrderBook)로 파싱하는 순수 함수만 둔다. 실제 WS
연결·재연결·하트비트는 어댑터 단계에서 검증한다.

Binance WS 이벤트 타입:
- "trade" -> 개별 체결가 (ticker/stream)
- "depthUpdate" / "24hrTicker" -> 호가/가격 정보
- "userDataStream" -> 개인 주문 이벤트 (private WS)

모든 파서 함수는 json.loads()가 이미 반환한 dict를 받는다.
필드 누락 시 FatalExchangeError를 던진다 (fail-closed).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from src.core.exceptions import FatalExchangeError
from src.data.models.market_data import OrderBook, OrderBookLevel, Ticker


def parse_binance_trade_message(message: dict[str, Any]) -> Ticker:
    """Binance "trade" 이벤트 메시지를 Ticker로 파싱.

    Binance WS trade 이벤트 형식:
    {
        "e": "trade",                    // 이벤트 타입
        "E": 1234567890,                 // 이벤트 시간
        "s": "BTCUSDT",                  // 심볼
        "t": 12345,                      // 체결 ID
        "p": "60000.00",                 // 체결 가격
        "q": "0.01",                     // 체결 수량
        "T": 1234567890,                  // 체결 시간
        "m": false,                       // 매수자 최우호 여부
    }
    """
    try:
        symbol_raw = message["s"]
        # Convert "BTCUSDT" -> "BTC/USDT"
        symbol = _raw_symbol_to_canonical(symbol_raw)
        price = Decimal(message["p"])
        quantity = Decimal(message["q"])
        timestamp_ms = message["T"]
        timestamp = datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC)
        return Ticker(
            symbol=symbol,
            exchange="binance",
            price=price,
            bid=price,  # trade 단일 메시지에 bid/ask 없음
            ask=price,
            volume_24h=quantity,
            timestamp=timestamp,
            source_type="primary",
        )
    except KeyError as exc:
        raise FatalExchangeError(f"Binance trade 메시지 필드 누락: {exc}") from exc


def parse_binance_orderbook_message(
    message: dict[str, Any], *, symbol: str | None = None
) -> OrderBook | None:
    """Binance "depthUpdate" 이벤트를 OrderBook으로 파싱.

    Binance WS depthUpdate 형식:
    {
        "e": "depthUpdate",              // 이벤트 타입
        "E": 1234567890,                 // 이벤트 시간
        "s": "BTCUSDT",                  // 심볼
        "b": [["60000.00", "0.01"], ...], // 호가 (bid)
        "a": [["60001.00", "0.02"], ...], // 호가 (ask)
    }

    symbol이 None이면 메시지 내부 "s" 필드에서 추출한다.
    빈 bids/asks 모두면 None을 반환 (스냅샷이 아님).
    """
    try:
        raw_symbol = message["s"]
        if symbol is None:
            symbol = _raw_symbol_to_canonical(raw_symbol)
        else:
            symbol = _raw_symbol_to_canonical(symbol)
        bids = _parse_price_quantity_pairs(message["b"])
        asks = _parse_price_quantity_pairs(message["a"])
        if not bids and not asks:
            return None
        return OrderBook(
            symbol=symbol,
            exchange="binance",
            bids=bids,
            asks=asks,
            timestamp=datetime.now(UTC),
        )
    except KeyError as exc:
        raise FatalExchangeError(f"Binance orderbook 메시지 필드 누락: {exc}") from exc


def parse_binance_24hr_ticker_message(message: dict[str, Any]) -> Ticker:
    """Binance "24hrTicker" 이벤트를 Ticker로 파싱.

    Binance WS 24hrTicker 형식:
    {
        "e": "24hrTicker",
        "E": 1234567890,
        "s": "BTCUSDT",
        "p": "1200.00",            // 24시간 가격 변동
        "P": "2.00",               // 가격 변동 %
        "w": "60000.00",           // 가중 평균 가격
        "b": "59900.00",           // 최우 매수호가
        "B": "0.01",               // 최우 매수호가 수량
        "a": "60100.00",           // 최우 매도호가
        "A": "0.02",               // 최우 매도호가 수량
        "h": "61000.00",           // 24시간 최고가
        "l": "59000.00",           // 24시간 최저가
        "v": "1000.00",            // 24시간 거래량
        "q": "60000000.00",        // 24시간 거래량(quote)
    }
    """
    try:
        symbol_raw = message["s"]
        symbol = _raw_symbol_to_canonical(symbol_raw)
        price = Decimal(message["w"])  # 가중 평균 가격을 현재가로 사용
        bid = Decimal(message["b"])
        ask = Decimal(message["a"])
        volume_24h = Decimal(message["v"])
        timestamp = datetime.fromtimestamp(message["E"] / 1000, tz=UTC)
        return Ticker(
            symbol=symbol,
            exchange="binance",
            price=price,
            bid=bid,
            ask=ask,
            volume_24h=volume_24h,
            timestamp=timestamp,
            source_type="primary",
        )
    except KeyError as exc:
        raise FatalExchangeError(f"Binance 24hrTicker 메시지 필드 누락: {exc}") from exc


def classify_binance_ws_event(message: dict[str, Any]) -> str:
    """Binance WS 메시지 이벤트 타입을 문자열로 분류.

    반환값: "trade", "depthUpdate", "24hrTicker", "kline",
    "userDataStream", "control", 또는 "unknown".
    """
    event_type = message.get("e", "")
    if event_type in ("trade", "depthUpdate", "24hrTicker", "kline"):
        return str(event_type)
    if event_type == "userDataStream":
        return "userDataStream"
    if event_type in (
        "outboundAccountPosition",
        "executionReport",
        "listOfOrders",
        "listOfPartialOrders",
    ):
        return "userDataStream"
    if event_type in ("", None):
        return "control"
    return "unknown"


def parse_listen_key_response(message: dict[str, Any]) -> str:
    """Binance userDataStream 시작 응답에서 listenKey 추출.

    응답 형식: {"listenKey": "abc123..."}
    listenKey가 없으면 FatalExchangeError.
    """
    try:
        return str(message["listenKey"])
    except KeyError as exc:
        raise FatalExchangeError(f"Binance listenKey 응답 필드 누락: {exc}") from exc


# ---- internal helpers ----


def _raw_symbol_to_canonical(raw: str) -> str:
    """ "BTCUSDT" -> "BTC/USDT". USDT 마켓만 Phase 1 지원."""
    if raw.endswith("USDT"):
        return f"{raw[:-4]}/USDT"
    raise FatalExchangeError(f"Binance 심볼 변환 실패: {raw}")


def _parse_price_quantity_pairs(pairs: list[list[Any]]) -> list[OrderBookLevel]:
    """[[price, qty], ...] -> [OrderBookLevel, ...]."""
    return [OrderBookLevel(price=Decimal(str(p)), quantity=Decimal(str(q))) for p, q in pairs]
