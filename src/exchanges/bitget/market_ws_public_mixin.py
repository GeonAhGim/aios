"""6.5 — BitgetAdapter 공개 WebSocket 채널 구독(ticker/candle/orderbook).

Spec: 02_exchange_adapter_v1.3.md#§2.1, 02b_bitget_api_v2_full_spec_v1.md#§6

공개 WebSocket(wss://ws.bitget.com/v2/ws/public, 공식 문서 기준 — 실제
채널 스키마는 라이브 검증 전까지 최선 추정치).

2026-09-03 task-1032(PLT-40a 선행) — `market_data_mixin.py`(735줄, P6
line_cap 초과)에서 순수 이동(동작 변경 0). 연결관리는
`market_ws_connection.py`, 메시지 파싱은 `market_ws_parsing.py` 참조.
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from src.data.models.market_data import Candle, OrderBook
from src.exchanges.bitget.market_ws_connection import (
    ConnectFn,
    ReconnectHook,
    _connect,
    _run_ws_subscription,
)
from src.exchanges.bitget.market_ws_parsing import (
    parse_candle_ws_message,
    parse_orderbook_ws_message,
    parse_ticker_ws_message,
)
from src.exchanges.bitget.symbols import to_bitget_symbol as _to_bitget_symbol
from src.exchanges.common.types import TickerCallback

logger = logging.getLogger(__name__)

WS_PUBLIC_URL = "wss://ws.bitget.com/v2/ws/public"

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

CandleCallback = Callable[[Candle], Awaitable[None]]
OrderBookCallback = Callable[[OrderBook], Awaitable[None]]


class BitgetMarketDataWsPublicMixin:
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
        직접 결합하지 않고 콜백으로 주입받는다(recovery.py와 동일 DI 패턴).

        FULL_AUDIT §2-B ② — 재연결 성공 시 REST get_ticker()로 한 번
        재동기화한 뒤(끊긴 동안 놓쳤을 수 있는 갱신을 메꿈) callback을
        호출한다 — 그다음 호출부가 넘긴 on_reconnected도 실행(이벤트
        발행 등은 여전히 호출부 책임)."""
        bitget_symbol = _to_bitget_symbol(symbol)
        subscribe_msg = {
            "op": "subscribe",
            "args": [{"instType": "SPOT", "channel": "ticker", "instId": bitget_symbol}],
        }

        async def resync_then_notify() -> None:
            try:
                ticker = await self.get_ticker(symbol)  # type: ignore[attr-defined]
                await callback(ticker)
            except Exception:  # noqa: BLE001 — 재동기화 실패로 재연결 자체를 막지 않음
                logger.warning("Bitget WS 재연결 후 REST 재동기화 실패(symbol=%s)", symbol)
            if on_reconnected is not None:
                await on_reconnected()

        async def on_message(message: dict[str, Any]) -> None:
            for ticker in parse_ticker_ws_message(message):
                await callback(ticker)

        await _run_ws_subscription(
            WS_PUBLIC_URL,
            subscribe_msg,
            on_message,
            connect_fn=connect_fn,
            on_reconnecting=on_reconnecting,
            on_reconnected=resync_then_notify,
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

        async def resync_then_notify() -> None:
            """FULL_AUDIT §2-B ② — 재연결 후 REST get_orderbook()으로 한
            번 재동기화(subscribe_ticker_stream과 동일 판단)."""
            try:
                book = await self.get_orderbook(symbol)  # type: ignore[attr-defined]
                await callback(book)
            except Exception:  # noqa: BLE001 — 재동기화 실패로 재연결 자체를 막지 않음
                logger.warning("Bitget WS 재연결 후 REST 재동기화 실패(symbol=%s)", symbol)
            if on_reconnected is not None:
                await on_reconnected()

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
            on_reconnected=resync_then_notify,
        )
