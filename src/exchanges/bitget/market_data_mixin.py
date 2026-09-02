"""6.4 / 6.5 — BitgetAdapter Market Data 메서드군 + WebSocket 구독/재연결.

Spec: 02_exchange_adapter_v1.3.md#§2.1, 02b_bitget_api_v2_full_spec_v1.md#§6

엔드포인트(2026-08-28 라이브 확인, GET /api/v2/spot/market/{tickers,
orderbook,candles}) 및 공개 WebSocket(wss://ws.bitget.com/v2/ws/public,
공식 문서 기준 — 실제 채널 스키마는 라이브 검증 전까지 최선 추정치).

2026-09-02 리팩터링(02b 스펙 §9 작업 분해 4번, WebSocket P0) — 기존
`subscribe_ticker_stream()`은 연결관리(재연결·백오프)와 메시지 파싱이
한 함수 안에 뒤섞여 있어 실소켓 없이는 전혀 테스트할 수 없었다(이
세션에서 확인 — 커밋 이력에 이 메서드의 테스트가 한 번도 없었음).
`_run_ws_subscription()`(연결관리 공통 루프)과 `_parse_*_message()`
(순수 파싱 함수, JSON 디코드된 dict만 받음)로 분리해 파싱 로직만이라도
실제 네트워크 없이 단위테스트 가능하게 만든다 — 연결관리 루프 자체는
`connect_fn`을 주입 가능하게 열어둬(기본값은 실제 websockets.connect)
가짜 연결로 재연결/백오프 동작까지 결정적으로 재현할 수 있다.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Protocol

from websockets.asyncio.client import connect as _default_connect
from websockets.exceptions import ConnectionClosed

from src.core.parser.candle_parser import parse_candles
from src.core.parser.orderbook_parser import parse_orderbook
from src.core.parser.ticker_parser import parse_ticker
from src.data.models.market_data import Candle, OrderBook, OrderBookLevel, Ticker
from src.exchanges.common.types import TickerCallback

logger = logging.getLogger(__name__)

WS_PUBLIC_URL = "wss://ws.bitget.com/v2/ws/public"

# AIOS 표준 timeframe -> Bitget REST candles granularity 파라미터
_GRANULARITY_MAP = {
    "1m": "1min",
    "5m": "5min",
    "15m": "15min",
    "30m": "30min",
    "1h": "1h",
    "4h": "4h",
    "1d": "1day",
}

# AIOS 표준 timeframe -> Bitget WebSocket candle 채널명(라이브 검증 필요 —
# 공식 문서상 관례적 표기, REST granularity 문자열과 형식이 다름).
_WS_CANDLE_CHANNEL_MAP = {
    "1m": "candle1m",
    "5m": "candle5m",
    "15m": "candle15m",
    "30m": "candle30m",
    "1h": "candle1H",
    "4h": "candle4H",
    "1d": "candle1D",
}

ReconnectHook = Callable[[], Awaitable[None]]
CandleCallback = Callable[[Candle], Awaitable[None]]
OrderBookCallback = Callable[[OrderBook], Awaitable[None]]
MessageHandler = Callable[[dict[str, Any]], Awaitable[None]]


class WsConnection(Protocol):
    async def send(self, message: str) -> None: ...
    def __aiter__(self) -> AsyncIterator[str]: ...


ConnectFn = Callable[[str], AbstractAsyncContextManager[WsConnection]]


def _connect(url: str) -> AbstractAsyncContextManager[WsConnection]:
    """`websockets.asyncio.client.connect`는 실제로는 URL 하나만으로도
    호출 가능한 비동기 컨텍스트 매니저를 반환하지만, 클래스 자체의 타입
    시그니처는 그보다 훨씬 넓다(헤더/ping 설정 등) — 테스트가 주입하는
    가짜 `connect_fn`과 정확히 같은 좁은 타입으로 맞추기 위한 얇은 래퍼."""
    return _default_connect(url)  # type: ignore[return-value]


def _to_bitget_symbol(canonical_symbol: str) -> str:
    """"BTC/USDT" -> "BTCUSDT" """
    return canonical_symbol.replace("/", "")


def _is_control_message(message: dict[str, Any]) -> bool:
    return message.get("event") in ("subscribe", "error")


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


async def _run_ws_subscription(
    url: str,
    subscribe_msg: dict[str, Any],
    on_message: MessageHandler,
    *,
    connect_fn: ConnectFn = _connect,
    on_reconnecting: ReconnectHook | None = None,
    on_reconnected: ReconnectHook | None = None,
    max_backoff_seconds: float = 30.0,
    sleep_fn: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """연결·구독·재연결(지수 백오프)을 전담하는 공통 루프 — 메시지
    자체의 의미는 모른다(on_message에 그대로 위임). 채널이 몇 개든
    이 루프 하나를 재사용한다(§2.1 재연결 책임 원칙, 로직 중복 방지)."""
    backoff = 1.0
    first_attempt = True

    while True:
        if not first_attempt and on_reconnecting is not None:
            await on_reconnecting()
        first_attempt = False
        try:
            async with connect_fn(url) as ws:
                await ws.send(json.dumps(subscribe_msg))
                if backoff > 1.0 and on_reconnected is not None:
                    await on_reconnected()
                backoff = 1.0
                async for raw_message in ws:
                    message = json.loads(raw_message)
                    await on_message(message)
        except (ConnectionClosed, OSError) as exc:
            logger.warning(
                "Bitget WS 연결 끊김(channel=%s): %s — %.1f초 후 재연결",
                subscribe_msg.get("args"),
                exc,
                backoff,
            )
            await sleep_fn(backoff)
            backoff = min(backoff * 2, max_backoff_seconds)


class BitgetMarketDataMixin:
    async def get_ticker(self, symbol: str) -> Ticker:
        raw = await self._request(  # type: ignore[attr-defined]
            "GET", "/api/v2/spot/market/tickers", params={"symbol": _to_bitget_symbol(symbol)}
        )
        return parse_ticker(raw["data"][0], "bitget")

    async def get_orderbook(self, symbol: str, depth: int = 20) -> OrderBook:
        raw = await self._request(  # type: ignore[attr-defined]
            "GET",
            "/api/v2/spot/market/orderbook",
            params={"symbol": _to_bitget_symbol(symbol), "limit": str(depth)},
        )
        return parse_orderbook(raw["data"], "bitget", symbol)

    async def get_ohlcv(self, symbol: str, timeframe: str, limit: int = 100) -> list[Candle]:
        granularity = _GRANULARITY_MAP.get(timeframe)
        if granularity is None:
            raise ValueError(f"지원하지 않는 timeframe: {timeframe}")
        raw = await self._request(  # type: ignore[attr-defined]
            "GET",
            "/api/v2/spot/market/candles",
            params={
                "symbol": _to_bitget_symbol(symbol),
                "granularity": granularity,
                "limit": str(limit),
            },
        )
        return parse_candles(raw["data"], "bitget", symbol, timeframe)

    async def subscribe_ticker_stream(
        self,
        symbol: str,
        callback: TickerCallback,
        *,
        on_reconnecting: ReconnectHook | None = None,
        on_reconnected: ReconnectHook | None = None,
        connect_fn: ConnectFn = _connect,
    ) -> None:
        """재연결은 이 메서드가 자체적으로 처리한다(지수 백오프, 최대 30초).
        on_reconnecting/on_reconnected는 상위 계층이 market.distrust.entered/
        exited를 발행할 수 있도록 하는 훅(§2.1 재연결 책임 원칙) — EventBus에
        직접 결합하지 않고 콜백으로 주입받는다(recovery.py와 동일 DI 패턴)."""
        bitget_symbol = _to_bitget_symbol(symbol)
        subscribe_msg = {
            "op": "subscribe",
            "args": [{"instType": "SPOT", "channel": "ticker", "instId": bitget_symbol}],
        }

        async def on_message(message: dict[str, Any]) -> None:
            for ticker in parse_ticker_ws_message(message):
                await callback(ticker)

        await _run_ws_subscription(
            WS_PUBLIC_URL,
            subscribe_msg,
            on_message,
            connect_fn=connect_fn,
            on_reconnecting=on_reconnecting,
            on_reconnected=on_reconnected,
        )

    async def subscribe_candle_stream(
        self,
        symbol: str,
        timeframe: str,
        callback: CandleCallback,
        *,
        on_reconnecting: ReconnectHook | None = None,
        on_reconnected: ReconnectHook | None = None,
        connect_fn: ConnectFn = _connect,
    ) -> None:
        """02b 스펙 §6(FD-2.2 실시간 캔들 — 현재는 REST 폴링만이던 것의
        실시간 대체). `ExchangeAdapter` ABC에는 아직 없음(trading_mixin.py
        확장 메서드들과 동일 원칙 — 소비하는 FD-2 호출부가 생기기 전까지
        Bitget 전용)."""
        channel = _WS_CANDLE_CHANNEL_MAP.get(timeframe)
        if channel is None:
            raise ValueError(f"지원하지 않는 timeframe: {timeframe}")
        bitget_symbol = _to_bitget_symbol(symbol)
        subscribe_msg = {
            "op": "subscribe",
            "args": [{"instType": "SPOT", "channel": channel, "instId": bitget_symbol}],
        }

        async def on_message(message: dict[str, Any]) -> None:
            for candle in parse_candle_ws_message(message, symbol=symbol, timeframe=timeframe):
                await callback(candle)

        await _run_ws_subscription(
            WS_PUBLIC_URL,
            subscribe_msg,
            on_message,
            connect_fn=connect_fn,
            on_reconnecting=on_reconnecting,
            on_reconnected=on_reconnected,
        )

    async def subscribe_orderbook_stream(
        self,
        symbol: str,
        callback: OrderBookCallback,
        *,
        depth_channel: str = "books",
        on_reconnecting: ReconnectHook | None = None,
        on_reconnected: ReconnectHook | None = None,
        connect_fn: ConnectFn = _connect,
    ) -> None:
        """02b 스펙 §6 — 실시간 호가창. `depth_channel`은 "books"(전체)/
        "books5"/"books15"(병합 깊이) 중 선택(라이브 검증 필요)."""
        bitget_symbol = _to_bitget_symbol(symbol)
        subscribe_msg = {
            "op": "subscribe",
            "args": [{"instType": "SPOT", "channel": depth_channel, "instId": bitget_symbol}],
        }

        async def on_message(message: dict[str, Any]) -> None:
            book = parse_orderbook_ws_message(message, symbol=symbol)
            if book is not None:
                await callback(book)

        await _run_ws_subscription(
            WS_PUBLIC_URL,
            subscribe_msg,
            on_message,
            connect_fn=connect_fn,
            on_reconnecting=on_reconnecting,
            on_reconnected=on_reconnected,
        )
