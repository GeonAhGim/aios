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
import base64
import hashlib
import hmac
import json
import logging
import time
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
from src.data.models.market_data import (
    Candle,
    OrderBook,
    OrderBookLevel,
    PublicTrade,
    SpotSymbolInfo,
    Ticker,
)
from src.data.models.trading import AccountBalance, Order, Position
from src.exchanges.bitget.futures_account_mixin import _row_to_position
from src.exchanges.bitget.trading_mixin import _row_to_order
from src.exchanges.common.types import TickerCallback

logger = logging.getLogger(__name__)

WS_PUBLIC_URL = "wss://ws.bitget.com/v2/ws/public"
WS_PRIVATE_URL = "wss://ws.bitget.com/v2/ws/private"

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
OrderCallback = Callable[[Order], Awaitable[None]]
AccountCallback = Callable[[AccountBalance], Awaitable[None]]
PositionCallback = Callable[[Position], Awaitable[None]]
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
    return message.get("event") in ("subscribe", "error", "login")


def _build_login_message(api_key: str, api_secret: str, api_passphrase: str) -> dict[str, Any]:
    """Private 채널 로그인 메시지 — 02b 스펙 §6 "Private 채널 로그인" 절
    기준 최선 추정치(공식 문서 조사, 2026-09-02). REST(`adapter.py::_sign`)와
    prehash 방식(HMAC-SHA256 후 base64)은 같지만 서명 대상 문자열이 다르다:
    REST는 실제 요청 경로/바디를 서명하는 반면, WS 로그인은 문서 관례상
    고정 문자열 "GET" + "/user/verify"를 쓴다(타임스탬프도 REST의 밀리초와
    달리 초 단위 문자열). 실제 Demo API 키로 라이브 검증 전까지 확정 아님."""
    timestamp = str(int(time.time()))
    prehash = timestamp + "GET" + "/user/verify"
    mac = hmac.new(api_secret.encode("utf-8"), prehash.encode("utf-8"), hashlib.sha256)
    sign = base64.b64encode(mac.digest()).decode("utf-8")
    return {
        "op": "login",
        "args": [
            {
                "apiKey": api_key,
                "passphrase": api_passphrase,
                "timestamp": timestamp,
                "sign": sign,
            }
        ],
    }


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


async def _run_ws_subscription(
    url: str,
    subscribe_msg: dict[str, Any],
    on_message: MessageHandler,
    *,
    pre_messages: list[dict[str, Any]] | None = None,
    connect_fn: ConnectFn = _connect,
    on_reconnecting: ReconnectHook | None = None,
    on_reconnected: ReconnectHook | None = None,
    max_backoff_seconds: float = 30.0,
    sleep_fn: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """연결·구독·재연결(지수 백오프)을 전담하는 공통 루프 — 메시지
    자체의 의미는 모른다(on_message에 그대로 위임). 채널이 몇 개든
    이 루프 하나를 재사용한다(§2.1 재연결 책임 원칙, 로직 중복 방지).
    `pre_messages`(예: Private 채널의 login)는 매 연결(최초 포함, 재연결
    포함)마다 subscribe_msg보다 먼저 순서대로 전송된다 — 재연결 시
    재로그인이 자동으로 이뤄지는 이유가 이것이다."""
    backoff = 1.0
    first_attempt = True

    while True:
        if not first_attempt and on_reconnecting is not None:
            await on_reconnecting()
        first_attempt = False
        try:
            async with connect_fn(url) as ws:
                for pre_message in pre_messages or []:
                    await ws.send(json.dumps(pre_message))
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

    async def get_history_candles(
        self, symbol: str, timeframe: str, *, limit: int = 100, end_time: str | None = None
    ) -> list[Candle]:
        """02b 스펙 §3.1(P1) — FD-2.3 백테스트 데이터 확장용. `end_time`은
        Bitget 밀리초 타임스탬프 문자열(그 시점 이전 데이터 조회, 페이지네이션
        용도) — 생략 시 최신 구간부터."""
        granularity = _GRANULARITY_MAP.get(timeframe)
        if granularity is None:
            raise ValueError(f"지원하지 않는 timeframe: {timeframe}")
        params: dict[str, Any] = {
            "symbol": _to_bitget_symbol(symbol),
            "granularity": granularity,
            "limit": str(limit),
        }
        if end_time is not None:
            params["endTime"] = end_time
        raw = await self._request(  # type: ignore[attr-defined]
            "GET", "/api/v2/spot/market/history-candles", params=params
        )
        return parse_candles(raw["data"], "bitget", symbol, timeframe)

    async def get_symbol_info(self, symbol: str | None = None) -> list[SpotSymbolInfo]:
        """02b 스펙 §3.1(P1)/§8 — FD-4.1(사전검증)이 필요로 하는 심볼
        규격. `symbol` 생략 시 전체 심볼 목록."""
        params: dict[str, Any] = {}
        if symbol is not None:
            params["symbol"] = _to_bitget_symbol(symbol)
        raw = await self._request(  # type: ignore[attr-defined]
            "GET", "/api/v2/spot/public/symbols", params=params or None
        )
        result = []
        for item in raw["data"]:
            price_precision = int(item.get("pricePrecision", "0"))
            quantity_precision = int(item.get("quantityPrecision", "0"))
            result.append(
                SpotSymbolInfo(
                    symbol=f"{item.get('baseCoin', '')}/{item.get('quoteCoin', '')}",
                    exchange="bitget",
                    base_coin=item.get("baseCoin", ""),
                    quote_coin=item.get("quoteCoin", ""),
                    tick_size=Decimal(1).scaleb(-price_precision),
                    lot_size=Decimal(1).scaleb(-quantity_precision),
                    min_trade_amount=Decimal(item.get("minTradeAmount", "0")),
                    status=item.get("status", ""),
                )
            )
        return result

    async def get_public_trades(self, symbol: str, *, limit: int = 100) -> list[PublicTrade]:
        """02b 스펙 §3.1(P1) — 시장 전체 체결 스트림(FD-2.6 데이터 신뢰도
        교차검증 보강용, 내 주문이 아닌 그 심볼의 전체 체결)."""
        raw = await self._request(  # type: ignore[attr-defined]
            "GET",
            "/api/v2/spot/market/fills",
            params={"symbol": _to_bitget_symbol(symbol), "limit": str(limit)},
        )
        return [
            PublicTrade(
                symbol=symbol,
                exchange="bitget",
                trade_id=item["tradeId"],
                price=Decimal(item["price"]),
                quantity=Decimal(item["size"]),
                side=item.get("side", ""),
                timestamp=datetime.fromtimestamp(int(item["ts"]) / 1000, tz=timezone.utc),
            )
            for item in raw["data"]
        ]

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

    async def subscribe_order_stream(
        self,
        callback: OrderCallback,
        *,
        inst_type: str = "SPOT",
        on_reconnecting: ReconnectHook | None = None,
        on_reconnected: ReconnectHook | None = None,
        connect_fn: ConnectFn = _connect,
    ) -> None:
        """02b 스펙 §6/§9 작업분해 4 — Private `orders` 채널(P0). FD-4.5
        (UNKNOWN 재조회)를 폴링 대신 실시간 이벤트로 대체하는 근본
        해결책 — 이 채널이 붙으면 기존 3회 폴링 재시도 로직은 "최후의
        폴백"으로 격하되고 정상 경로는 실시간 확인이 된다(호출부 연결은
        별도 leaf). 로그인 메커니즘은 `_build_login_message()` docstring
        참조 — 라이브 검증 전까지 최선 추정치. `ExchangeAdapter` ABC에는
        아직 없음(다른 확장 메서드들과 동일 원칙, 모듈 docstring 참조)."""
        login_msg = _build_login_message(
            self._api_key,  # type: ignore[attr-defined]
            self._api_secret,  # type: ignore[attr-defined]
            self._api_passphrase,  # type: ignore[attr-defined]
        )
        subscribe_msg = {
            "op": "subscribe",
            "args": [{"instType": inst_type, "channel": "orders", "instId": "default"}],
        }

        async def on_message(message: dict[str, Any]) -> None:
            for order in parse_order_ws_message(message):
                await callback(order)

        await _run_ws_subscription(
            WS_PRIVATE_URL,
            subscribe_msg,
            on_message,
            pre_messages=[login_msg],
            connect_fn=connect_fn,
            on_reconnecting=on_reconnecting,
            on_reconnected=on_reconnected,
        )

    async def subscribe_account_stream(
        self,
        callback: AccountCallback,
        *,
        inst_type: str = "SPOT",
        on_reconnecting: ReconnectHook | None = None,
        on_reconnected: ReconnectHook | None = None,
        connect_fn: ConnectFn = _connect,
    ) -> None:
        """02b 스펙 §6(P1) — Private `account` 채널. FD-16.4(실행
        모니터링)이 현재 폴링 기반인 잔고 확인을 실시간으로 보강할 수
        있는 후보(호출부 연결은 별도 leaf). 로그인은
        `subscribe_order_stream`과 동일 메커니즘(§6 "Private 채널 로그인"
        절) — 라이브 검증 전까지 최선 추정치."""
        login_msg = _build_login_message(
            self._api_key,  # type: ignore[attr-defined]
            self._api_secret,  # type: ignore[attr-defined]
            self._api_passphrase,  # type: ignore[attr-defined]
        )
        subscribe_msg = {
            "op": "subscribe",
            "args": [{"instType": inst_type, "channel": "account", "instId": "default"}],
        }

        async def on_message(message: dict[str, Any]) -> None:
            for balance in parse_account_ws_message(message):
                await callback(balance)

        await _run_ws_subscription(
            WS_PRIVATE_URL,
            subscribe_msg,
            on_message,
            pre_messages=[login_msg],
            connect_fn=connect_fn,
            on_reconnecting=on_reconnecting,
            on_reconnected=on_reconnected,
        )

    async def subscribe_positions_stream(
        self,
        callback: PositionCallback,
        *,
        inst_type: str = "USDT-FUTURES",
        on_reconnecting: ReconnectHook | None = None,
        on_reconnected: ReconnectHook | None = None,
        connect_fn: ConnectFn = _connect,
    ) -> None:
        """02b 스펙 §6(P1) — Private `positions` 채널(선물 전용). Phase 1은
        크립토 현물 전용(06번 §6.1)이라 아직 소비하는 호출부가 없다 —
        API 연동만 우선 완료해둔다(다른 확장 메서드와 동일 원칙)."""
        login_msg = _build_login_message(
            self._api_key,  # type: ignore[attr-defined]
            self._api_secret,  # type: ignore[attr-defined]
            self._api_passphrase,  # type: ignore[attr-defined]
        )
        subscribe_msg = {
            "op": "subscribe",
            "args": [{"instType": inst_type, "channel": "positions", "instId": "default"}],
        }

        async def on_message(message: dict[str, Any]) -> None:
            for position in parse_position_ws_message(message):
                await callback(position)

        await _run_ws_subscription(
            WS_PRIVATE_URL,
            subscribe_msg,
            on_message,
            pre_messages=[login_msg],
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
