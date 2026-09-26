"""BR-21e -- OKX WebSocket: real-time ticker/orderbook/private-order events.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md#BR-21,
docs/exchanges/ADDING_AN_EXCHANGE.md step 1/6(e).

Endpoints (verified 2026-09-26 against the official OKX v5 API reference,
www.okx.com/docs-v5/en, "WebSocket" section):
- Public: wss://ws.okx.com:8443/ws/v5/public. `{"op":"subscribe","args":
  [{"channel":"tickers","instId":<symbol>}]}` -- push frame:
  `{"arg":{"channel":"tickers",...},"data":[{instId,last,bidPx,askPx,
  vol24h,ts}]}`. Same shape for `{"channel":"books","instId":...}` --
  push `data[]` rows: `{instId,asks:[[px,sz,_,numOrders]],bids:[...],ts}`.
- Private: wss://ws.okx.com:8443/ws/v5/private. Requires login first:
  `{"op":"login","args":[{"apiKey":...,"passphrase":...,"timestamp":
  <unix-seconds-string>,"sign":...}]}` -- note this timestamp is Unix
  **seconds** (not OKX REST's ISO-8601-millis, and not epoch-ms either) --
  the WS login prehash is `timestamp + "GET" + "/users/self/verify"`.
  After a successful login ack, `{"op":"subscribe","args":[{"channel":
  "orders","instType":"ANY"}]}` -- push `data[]` rows share the same
  order-row shape as `okx/account_mixin.py::_row_to_order`'s input.
- Ack/error frames: `{"event":"subscribe"|"login","arg":{...}}` on
  success, `{"event":"error","code":...,"msg":...}` on failure.
  Heartbeat: plain-text "ping"/"pong" (not JSON), same shape as Bitget's.

Connection/reconnect/heartbeat/ack-validation is delegated entirely to the
shared `src.exchanges.common.ws_session.WsSession` (L4-19) -- OKX's ack
shape (JSON `{"event":...}`) and heartbeat (plain "ping"/"pong" text) both
fit that session's existing `AckValidator`/`HeartbeatSpec` contract with no
new plumbing, so this module only supplies parsing + the
`ExchangeAdapter`-facing subscribe methods (keeps this file under the
300-line file-policy cap by not duplicating `WsSession`'s loop -- same
reuse principle Bitget/Kiwoom's own connection-file docstrings cite).

Malformed-frame handling: `WsSession._decode` already rejects a non-JSON/
non-dict frame (`WsProtocolError`) before this module ever sees it. This
module additionally rejects a well-formed JSON *data* frame whose rows are
missing the fields the parsers need -- `FatalExchangeError`, never a
silent drop (ADDING_AN_EXCHANGE.md step 1 warning: silently dropping a
malformed data row would hide a real parsing bug from the caller instead
of surfacing it).
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from src.core.exceptions import FatalExchangeError
from src.data.models.market_data import OrderBook, OrderBookLevel, Ticker
from src.data.models.trading import Order
from src.exchanges.common.types import TickerCallback
from src.exchanges.common.ws_session import (
    NOT_ACK,
    AckResult,
    ConnectFn,
    WsSession,
    default_connect,
)
from src.exchanges.okx.account_mixin import row_to_order
from src.exchanges.okx.auth import sign_request

WS_PUBLIC_URL = "wss://ws.okx.com:8443/ws/v5/public"
WS_PRIVATE_URL = "wss://ws.okx.com:8443/ws/v5/private"

_LOGIN_METHOD = "GET"
_LOGIN_REQUEST_PATH = "/users/self/verify"

OrderBookCallback = Callable[[OrderBook], Awaitable[None]]
OrderCallback = Callable[[Order], Awaitable[None]]


class _OKXWsAuthClient(Protocol):
    """Attributes this module needs of `self` for the private channel's
    login handshake -- satisfied by `OKXHTTPClient` (auth.py) once
    `factory.py` assembles the full adapter."""

    _api_key: str
    _api_secret: str
    _api_passphrase: str


def _ack_validator(message: dict[str, Any]) -> AckResult:
    event = message.get("event")
    if event is None:
        return NOT_ACK
    if event == "error":
        return AckResult(is_ack=True, ok=False, detail=str(message.get("msg", "")))
    return AckResult(is_ack=True, ok=True, detail=event)


def _login_args(
    client: _OKXWsAuthClient, *, now: Callable[[], float] = time.time
) -> list[dict[str, str]]:
    timestamp = str(int(now()))
    sign = sign_request(client._api_secret, timestamp, _LOGIN_METHOD, _LOGIN_REQUEST_PATH)
    return [
        {
            "apiKey": client._api_key,
            "passphrase": client._api_passphrase,
            "timestamp": timestamp,
            "sign": sign,
        }
    ]


def _channel_rows(message: dict[str, Any], channel: str) -> list[dict[str, Any]] | None:
    if message.get("arg", {}).get("channel") != channel:
        return None
    return message.get("data") or []


def parse_ticker_message(message: dict[str, Any]) -> list[Ticker]:
    rows = _channel_rows(message, "tickers")
    if rows is None:
        return []
    tickers: list[Ticker] = []
    for row in rows:
        try:
            tickers.append(
                Ticker(
                    symbol=row["instId"],
                    exchange="okx",
                    price=Decimal(str(row["last"])),
                    bid=Decimal(str(row.get("bidPx") or row["last"])),
                    ask=Decimal(str(row.get("askPx") or row["last"])),
                    volume_24h=Decimal(str(row.get("vol24h", "0"))),
                    timestamp=datetime.fromtimestamp(int(row["ts"]) / 1000, tz=timezone.utc),
                    source_type="primary",
                )
            )
        except (KeyError, InvalidOperation, ValueError) as exc:
            raise FatalExchangeError(
                f"OKX tickers WS frame missing/invalid field: {exc}, row={row!r}"
            ) from exc
    return tickers


def parse_orderbook_message(message: dict[str, Any]) -> list[OrderBook]:
    rows = _channel_rows(message, "books")
    if rows is None:
        return []
    books: list[OrderBook] = []
    for row in rows:
        try:
            bids = [
                OrderBookLevel(price=Decimal(str(level[0])), quantity=Decimal(str(level[1])))
                for level in row["bids"]
            ]
            asks = [
                OrderBookLevel(price=Decimal(str(level[0])), quantity=Decimal(str(level[1])))
                for level in row["asks"]
            ]
            timestamp = datetime.fromtimestamp(int(row["ts"]) / 1000, tz=timezone.utc)
        except (KeyError, IndexError, InvalidOperation, ValueError) as exc:
            raise FatalExchangeError(
                f"OKX books WS frame missing/invalid field: {exc}, row={row!r}"
            ) from exc
        books.append(
            OrderBook(
                symbol=row["instId"], exchange="okx", bids=bids, asks=asks, timestamp=timestamp
            )
        )
    return books


def parse_order_message(message: dict[str, Any]) -> list[Order]:
    rows = _channel_rows(message, "orders")
    if rows is None:
        return []
    return [row_to_order(row) for row in rows]


class OKXWebSocketMixin:
    async def subscribe_ticker_stream(
        self,
        symbol: str,
        callback: TickerCallback,
        *,
        connect_fn: ConnectFn = default_connect,
    ) -> None:
        """`ExchangeAdapter` abstract method -- public `tickers` channel."""
        session = WsSession(
            WS_PUBLIC_URL,
            venue="okx",
            channel="tickers",
            ack_validator=_ack_validator,
            connect_fn=connect_fn,
        )

        async def handler(message: dict[str, Any]) -> None:
            for ticker in parse_ticker_message(message):
                await callback(ticker)

        await session.run(
            [{"op": "subscribe", "args": [{"channel": "tickers", "instId": symbol}]}], handler
        )

    async def subscribe_orderbook_stream(
        self,
        symbol: str,
        callback: OrderBookCallback,
        *,
        connect_fn: ConnectFn = default_connect,
    ) -> None:
        """Not part of the `ExchangeAdapter` ABC yet (same convention as
        Bitget/Kiwoom's own `subscribe_orderbook_stream` extensions) --
        public `books` channel."""
        session = WsSession(
            WS_PUBLIC_URL,
            venue="okx",
            channel="books",
            ack_validator=_ack_validator,
            connect_fn=connect_fn,
        )

        async def handler(message: dict[str, Any]) -> None:
            for book in parse_orderbook_message(message):
                await callback(book)

        await session.run(
            [{"op": "subscribe", "args": [{"channel": "books", "instId": symbol}]}], handler
        )

    async def subscribe_order_stream(
        self: _OKXWsAuthClient,
        callback: OrderCallback,
        *,
        connect_fn: ConnectFn = default_connect,
    ) -> None:
        """`ExchangeAdapter`'s L4-13 extension -- private `orders` channel
        (account-wide, `instType="ANY"` covers spot/margin/futures alike;
        only spot rows are meaningful given this leaf's scope)."""
        session = WsSession(
            WS_PRIVATE_URL,
            venue="okx",
            channel="orders",
            ack_validator=_ack_validator,
            connect_fn=connect_fn,
            pre_messages_factory=lambda: [{"op": "login", "args": _login_args(self)}],
        )

        async def handler(message: dict[str, Any]) -> None:
            for order in parse_order_message(message):
                await callback(order)

        await session.run(
            [{"op": "subscribe", "args": [{"channel": "orders", "instType": "ANY"}]}], handler
        )
