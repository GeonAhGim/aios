"""BR-23(task-7572) -- Kiwoom WebSocket connection/reconnect loop.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md#BR-23.
Split out of websocket.py per CLAUDE.md file-policy (ADR-2026-09-10-C) --
mirrors kis/websocket_connection.py's split (connection/loop vs. mixin vs.
parsing in separate files).

Verified 2026-09-26 (WebFetch, github.com/Kiwoom-Securities/Kiwoom-REST-API,
`kiwoom/core/ws_client.py` + `kiwoom/core/auth.py`, official Kiwoom
Securities REST API client repo -- same source cited by trading_mixin.py,
task-7571):
- WS URL: real `wss://api.kiwoom.com:10000`, mock `wss://mockapi.kiwoom.com:10000`,
  path `/api/dostk/websocket` (`get_ws_base_url(mode)` in `auth.py`).
- Login handshake: client sends `{"trnm": "LOGIN", "token": <access_token>}`
  first; server echoes back `trnm: "LOGIN"` as an ack (`_is_login_message`).
- Keepalive: server sends `{"trnm": "PING", ...}`; client must echo the same
  payload back verbatim (`_is_ping_message` check in `ws_client.py`) or the
  server drops the connection (same "must-pong" shape as KIS's PINGPONG,
  `kis/websocket_connection.py`).
- Subscribe: `{"trnm": "REG", "grp_no": "1", "refresh": "1",
  "data": [{"item": [<symbols>], "type": [<type_code>]}]}`. Type codes
  confirmed by example file name: "0B" stock trade tick, "0D" order book
  depth, "00" order fill notice.

Structural pattern follows `kis/websocket_connection.py`'s self-contained
reconnect loop (own JSON envelope, not a pipe/caret frame like KIS, so the
loop is not shared) -- kept local here for the same reason
`trading_mixin.py`'s `_KiwoomOrderClient` Protocol is local: `auth.py`
(task-7569) is a sibling leaf in flight in parallel and cannot yet be
imported for a `KiwoomHTTPClient`/token-provider type. Once
`KiwoomAuthClient` lands, its `_ensure_token()` method already satisfies
`_KiwoomWsAuthClient` below (same method name) with no changes needed here.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from typing import Any, Protocol, cast

from websockets.asyncio.client import connect as _default_connect
from websockets.exceptions import ConnectionClosed

from src.core.exceptions import FatalExchangeError

logger = logging.getLogger(__name__)

WS_REAL_URL = "wss://api.kiwoom.com:10000/api/dostk/websocket"
WS_PAPER_URL = "wss://mockapi.kiwoom.com:10000/api/dostk/websocket"

TYPE_STOCK_TRADE = "0B"
TYPE_ORDERBOOK = "0D"
TYPE_ORDER_FILL = "00"

ReconnectHook = Callable[[], Awaitable[None]]


class WsConnection(Protocol):
    async def send(self, message: str) -> None: ...
    def __aiter__(self) -> AsyncIterator[str]: ...


ConnectFn = Callable[[str], AbstractAsyncContextManager[WsConnection]]


def connect(url: str) -> AbstractAsyncContextManager[WsConnection]:
    """Same `cast()`-as-type-assertion reasoning as
    `kis/websocket_connection.py::_connect` -- `websockets`' own return type
    isn't recognized by mypy as a subtype of `AbstractAsyncContextManager`."""
    return cast(AbstractAsyncContextManager[WsConnection], _default_connect(url))


class KiwoomWsAuthClient(Protocol):
    """Minimal contract this module needs of `self` -- satisfied by
    `KiwoomAuthClient._ensure_token` once task-7569 lands (module docstring)."""

    is_paper_trading: bool

    async def _ensure_token(self) -> str: ...


def build_login_message(token: str) -> dict[str, Any]:
    return {"trnm": "LOGIN", "token": token}


def build_register_message(
    symbols: list[str], type_code: str, *, grp_no: str = "1"
) -> dict[str, Any]:
    return {
        "trnm": "REG",
        "grp_no": grp_no,
        "refresh": "1",
        "data": [{"item": symbols, "type": [type_code]}],
    }


def _is_login_ack(message: dict[str, Any]) -> bool:
    return message.get("trnm") == "LOGIN"


def _is_ping(message: dict[str, Any]) -> bool:
    return message.get("trnm") == "PING"


def _login_failed(message: dict[str, Any]) -> bool:
    return_code = message.get("return_code")
    return return_code not in (None, 0, "0")


async def run_kiwoom_ws_subscription(
    url: str,
    token: str,
    register_msg: dict[str, Any],
    on_message: Callable[[dict[str, Any]], Awaitable[None]],
    *,
    connect_fn: ConnectFn = connect,
    on_reconnecting: ReconnectHook | None = None,
    on_reconnected: ReconnectHook | None = None,
    max_backoff_seconds: float = 30.0,
    sleep_fn: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Kiwoom-specific connection/reconnect loop -- the LOGIN handshake and
    verbatim PING echo (module docstring) mean the existing KIS/Bitget loops
    can't be reused as-is (the "reuse responsibility, not code" principle
    from `kis/websocket_connection.py`'s own module docstring applies the
    same way here: protocols differ fundamentally, so a local loop is kept).
    """
    backoff = 1.0
    first_attempt = True
    logged_in = False
    registered = False

    async def handle_frame(raw: str, ws: WsConnection) -> None:
        nonlocal logged_in, registered
        message = json.loads(raw)
        if _is_ping(message):
            await ws.send(raw)
            return
        if _is_login_ack(message):
            if _login_failed(message):
                raise FatalExchangeError(f"Kiwoom WS login failed: {message.get('return_msg')}")
            logged_in = True
            if not registered:
                await ws.send(json.dumps(register_msg))
                registered = True
            return
        if not logged_in:
            # A data frame arriving before the login ack would violate the
            # documented handshake order -- ignore it defensively rather than
            # dispatching unauthenticated data.
            return
        await on_message(message)

    while True:
        if not first_attempt and on_reconnecting is not None:
            await on_reconnecting()
        first_attempt = False
        try:
            async with connect_fn(url) as ws:
                await ws.send(json.dumps(build_login_message(token)))
                logged_in = False
                registered = False
                if backoff > 1.0 and on_reconnected is not None:
                    await on_reconnected()
                backoff = 1.0
                async for raw in ws:
                    await handle_frame(raw, ws)
        except (ConnectionClosed, OSError) as exc:
            logger.warning("Kiwoom WS connection lost: %s -- reconnecting in %.1fs", exc, backoff)
            await sleep_fn(backoff)
            backoff = min(backoff * 2, max_backoff_seconds)
