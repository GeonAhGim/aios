"""6.4 / 6.5 — BitgetAdapter Market Data 메서드군 + WebSocket 구독/재연결.

Spec: 02_exchange_adapter_v1.2.md#§2.1

엔드포인트(2026-08-28 라이브 확인, GET /api/v2/spot/market/{tickers,
orderbook,candles}) 및 공개 WebSocket(wss://ws.bitget.com/v2/ws/public,
공식 문서 기준 — 실제 채널 스키마는 라이브 검증 전까지 최선 추정치).
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from src.core.parser.candle_parser import parse_candles
from src.core.parser.orderbook_parser import parse_orderbook
from src.core.parser.ticker_parser import parse_ticker
from src.data.models.market_data import Candle, OrderBook, Ticker
from src.exchanges.common.types import TickerCallback

logger = logging.getLogger(__name__)

WS_PUBLIC_URL = "wss://ws.bitget.com/v2/ws/public"

# AIOS 표준 timeframe -> Bitget candles granularity 파라미터
_GRANULARITY_MAP = {
    "1m": "1min",
    "5m": "5min",
    "15m": "15min",
    "30m": "30min",
    "1h": "1h",
    "4h": "4h",
    "1d": "1day",
}

ReconnectHook = Callable[[], Awaitable[None]]


def _to_bitget_symbol(canonical_symbol: str) -> str:
    """"BTC/USDT" -> "BTCUSDT" """
    return canonical_symbol.replace("/", "")


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
        backoff = 1.0
        first_attempt = True

        while True:
            if not first_attempt and on_reconnecting is not None:
                await on_reconnecting()
            first_attempt = False
            try:
                async with connect(WS_PUBLIC_URL) as ws:
                    await ws.send(json.dumps(subscribe_msg))
                    if backoff > 1.0 and on_reconnected is not None:
                        await on_reconnected()
                    backoff = 1.0
                    async for raw_message in ws:
                        message = json.loads(raw_message)
                        if message.get("event") in ("subscribe", "error"):
                            continue
                        for item in message.get("data", []):
                            await callback(parse_ticker(item, "bitget"))
            except (ConnectionClosed, OSError) as exc:
                logger.warning(
                    "Bitget WS 연결 끊김(symbol=%s): %s — %.1f초 후 재연결", symbol, exc, backoff
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)
