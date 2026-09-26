"""BR-22e -- BinanceWebSocketMixin: real-time ticker/orderbook/private order
events (exchange onboarding step 6(e), docs/exchanges/ADDING_AN_EXCHANGE.md).

Spec: `ExchangeAdapter.subscribe_ticker_stream` (abstract) + `subscribe_order_stream`
(L4-13 extension) in src/exchanges/common/adapter.py.

Streams (verified 2026-09-26 against the official Binance Spot API
documentation, github.com/binance/binance-spot-api-docs, `web-socket-streams.md`
+ `user-data-stream.md`):
- `wss://stream.binance.com:9443/ws/<symbol>@ticker` -- Individual Symbol
  Ticker Stream. No subscribe message needed -- the stream starts on
  connect, and every frame is a data frame (there is no ack/login
  handshake for public raw streams), unlike Bitget/Kiwoom's WS. Payload:
  `e` (event type, "24hrTicker"), `s` (symbol), `c` (last price), `b`/`a`
  (best bid/ask), `v` (24h volume), `E` (event time, ms).
- `wss://stream.binance.com:9443/ws/<symbol>@depth<levels>` -- Partial Book
  Depth Stream. Payload: `lastUpdateId`, `bids`/`asks` (each `[price, qty]`
  string pairs) -- **no `symbol` field in the payload itself** (the stream
  URL already scopes it), so `parse_depth_message` takes `symbol` as a
  separate argument. Not part of the `ExchangeAdapter` ABC (same "venue
  extension, not required yet" convention as Kiwoom's
  `subscribe_orderbook_stream`).
- User Data Stream (private): `POST /api/v3/userDataStream` (endpoint
  security type USER_STREAM -- API-KEY header only, no HMAC signature)
  returns `{"listenKey": ...}`; connecting to
  `wss://.../ws/<listenKey>` then streams account events. Only the
  `executionReport` event type (order state changes) maps to `Order` --
  other event types on this stream (`outboundAccountPosition`,
  `balanceUpdate`, ...) are valid and silently skipped, not errors.

Known scope gap (not a guess, an explicit omission): a listenKey expires
after 60 minutes without a keepalive `PUT /api/v3/userDataStream` call --
this file does not implement that periodic renewal, so a long-lived
`subscribe_order_stream` connection will eventually be dropped server-side.
Wiring the renewal loop is left to the caller/next leaf.

Reconnection/backoff reuses the venue-agnostic `WsSession`
(src/exchanges/common/ws_session.py, L4-19) rather than reimplementing it.
Heartbeat: Binance's WS servers ping at the protocol frame level (RFC 6455
control frames, ~3 min interval) and the `websockets` library used by
`ws_session.py`'s `default_connect` answers those automatically without any
application code -- sending an *application-level* text "ping" (WsSession's
default heartbeat behavior) to a raw stream is not a documented Binance
convention, so `_NO_APP_HEARTBEAT` sets an interval effectively longer than
any real connection's lifetime to avoid sending an unsolicited frame the
docs do not describe.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol

from src.core.exceptions import FatalExchangeError
from src.data.models.base import AssetClass
from src.data.models.market_data import OrderBook, OrderBookLevel, Ticker
from src.data.models.trading import Order, OrderSide, OrderStatus, OrderType
from src.exchanges.common.types import TickerCallback
from src.exchanges.common.ws_session import NOT_ACK, AckResult, ConnectFn, HeartbeatSpec, WsSession

_LIVE_WS_BASE = "wss://stream.binance.com:9443/ws"
_TESTNET_WS_BASE = "wss://testnet.binance.vision/ws"
USER_DATA_STREAM_PATH = "/api/v3/userDataStream"
_EXECUTION_REPORT_EVENT = "executionReport"

# See module docstring "Heartbeat" -- effectively disables WsSession's
# app-level ping so this mixin only relies on the WS library's own
# protocol-level ping/pong.
_NO_APP_HEARTBEAT = HeartbeatSpec(interval_sec=1e9)

_ORDER_STATUS_MAP: dict[str, OrderStatus] = {
    "NEW": OrderStatus.ACKNOWLEDGED,
    "PARTIALLY_FILLED": OrderStatus.PARTIALLY_FILLED,
    "FILLED": OrderStatus.FILLED,
    "CANCELED": OrderStatus.CANCELLED,
    "REJECTED": OrderStatus.REJECTED,
    "EXPIRED": OrderStatus.EXPIRED,
}

OrderBookCallback = Callable[[OrderBook], Awaitable[None]]
OrderCallback = Callable[[Order], Awaitable[None]]


def _no_ack(_message: dict[str, Any]) -> AckResult:
    """Public raw streams and the user-data stream have no subscribe/login
    ack frame -- every message is data (module docstring)."""
    return NOT_ACK


def parse_ticker_message(raw: dict[str, Any]) -> Ticker:
    """`<symbol>@ticker` payload -> `Ticker`. Raises on a non-dict frame or
    a missing required field -- never returns a partially-filled `Ticker`."""
    if not isinstance(raw, dict):
        raise FatalExchangeError(f"Binance ticker WS frame is not an object: {raw!r}")
    try:
        return Ticker(
            symbol=raw["s"],
            exchange="binance",
            price=Decimal(str(raw["c"])),
            bid=Decimal(str(raw["b"])),
            ask=Decimal(str(raw["a"])),
            volume_24h=Decimal(str(raw["v"])),
            timestamp=datetime.fromtimestamp(int(raw["E"]) / 1000, tz=UTC),
            source_type="primary",
        )
    except (KeyError, ValueError, TypeError) as exc:
        raise FatalExchangeError(f"Binance ticker WS frame malformed: {exc}") from exc


def parse_depth_message(raw: dict[str, Any], *, symbol: str) -> OrderBook:
    """`<symbol>@depth<levels>` payload -> `OrderBook`. The payload has no
    `symbol` field (module docstring), so the caller supplies it."""
    if not isinstance(raw, dict):
        raise FatalExchangeError(f"Binance depth WS frame is not an object: {raw!r}")
    try:
        bids = [OrderBookLevel(price=Decimal(p), quantity=Decimal(q)) for p, q in raw["bids"]]
        asks = [OrderBookLevel(price=Decimal(p), quantity=Decimal(q)) for p, q in raw["asks"]]
    except (KeyError, ValueError, TypeError) as exc:
        raise FatalExchangeError(f"Binance depth WS frame malformed: {exc}") from exc
    return OrderBook(
        symbol=symbol, exchange="binance", bids=bids, asks=asks, timestamp=datetime.now(UTC)
    )


def parse_execution_report_message(raw: dict[str, Any]) -> Order | None:
    """User Data Stream payload -> `Order`, or `None` for a valid non-order
    event type (`outboundAccountPosition`/`balanceUpdate`/...). Raises on a
    non-dict frame, a frame with no `e` at all, or an `executionReport`
    missing a required field -- `None` means "not an order event," never
    "malformed"."""
    if not isinstance(raw, dict):
        raise FatalExchangeError(f"Binance user-data WS frame is not an object: {raw!r}")
    try:
        event_type = raw["e"]
    except KeyError as exc:
        raise FatalExchangeError(f"Binance user-data WS frame missing 'e': {raw!r}") from exc
    if event_type != _EXECUTION_REPORT_EVENT:
        return None
    try:
        return Order(
            exchange_order_id=f"{raw['s']}:{raw['i']}",
            client_order_id=str(raw.get("c", "")),
            strategy_id="",
            strategy_version="",
            symbol=raw["s"],
            exchange="binance",
            side=OrderSide(raw["S"]),
            order_type=OrderType(raw["o"]),
            quantity=Decimal(str(raw["q"])),
            status=_ORDER_STATUS_MAP.get(raw["X"], OrderStatus.UNKNOWN),
            filled_quantity=Decimal(str(raw["z"])),
            asset_class=AssetClass.CRYPTO,
        )
    except (KeyError, ValueError) as exc:
        raise FatalExchangeError(
            f"Binance executionReport WS frame missing/invalid expected field: {exc}"
        ) from exc


class _BinanceWsAuthClient(Protocol):
    is_paper_trading: bool

    async def _request(
        self, method: str, path: str, *, params: dict[str, Any] | None = None
    ) -> Any: ...

    def _ws_base_url(self) -> str: ...

    async def _get_listen_key(self) -> str: ...


class BinanceWebSocketMixin:
    def _ws_base_url(self: _BinanceWsAuthClient) -> str:
        return _TESTNET_WS_BASE if self.is_paper_trading else _LIVE_WS_BASE

    async def _get_listen_key(self: _BinanceWsAuthClient) -> str:
        raw = await self._request("POST", USER_DATA_STREAM_PATH)
        if not isinstance(raw, dict) or "listenKey" not in raw:
            raise FatalExchangeError(f"Binance userDataStream response missing listenKey: {raw!r}")
        return str(raw["listenKey"])

    async def subscribe_ticker_stream(
        self: _BinanceWsAuthClient,
        symbol: str,
        callback: TickerCallback,
        *,
        connect_fn: ConnectFn | None = None,
    ) -> None:
        """`ExchangeAdapter` abstract method."""
        url = f"{self._ws_base_url()}/{symbol.lower()}@ticker"
        session = _build_session(url, channel="ticker", connect_fn=connect_fn)

        async def handler(message: dict[str, Any]) -> None:
            await callback(parse_ticker_message(message))

        await session.run([], handler)

    async def subscribe_orderbook_stream(
        self: _BinanceWsAuthClient,
        symbol: str,
        callback: OrderBookCallback,
        *,
        connect_fn: ConnectFn | None = None,
    ) -> None:
        """Not part of the `ExchangeAdapter` ABC yet (same convention as
        Kiwoom's `subscribe_orderbook_stream`) -- real-time partial book
        depth."""
        url = f"{self._ws_base_url()}/{symbol.lower()}@depth20"
        session = _build_session(url, channel="depth", connect_fn=connect_fn)

        async def handler(message: dict[str, Any]) -> None:
            await callback(parse_depth_message(message, symbol=symbol))

        await session.run([], handler)

    async def subscribe_order_stream(
        self: _BinanceWsAuthClient,
        callback: OrderCallback,
        *,
        connect_fn: ConnectFn | None = None,
    ) -> None:
        """`ExchangeAdapter`'s L4-13 extension -- private order-fill events
        via the User Data Stream (module docstring's listenKey flow)."""
        listen_key = await self._get_listen_key()
        url = f"{self._ws_base_url()}/{listen_key}"
        session = _build_session(url, channel="orders", connect_fn=connect_fn)

        async def handler(message: dict[str, Any]) -> None:
            order = parse_execution_report_message(message)
            if order is not None:
                await callback(order)

        await session.run([], handler)


def _build_session(url: str, *, channel: str, connect_fn: ConnectFn | None) -> WsSession:
    kwargs: dict[str, Any] = {
        "venue": "binance",
        "channel": channel,
        "ack_validator": _no_ack,
        "heartbeat": _NO_APP_HEARTBEAT,
    }
    if connect_fn is not None:
        kwargs["connect_fn"] = connect_fn
    return WsSession(url, **kwargs)
