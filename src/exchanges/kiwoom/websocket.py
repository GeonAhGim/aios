"""BR-23(task-7572) -- KiwoomWebSocketMixin: real-time trade/orderbook/private
order-fill events (exchange onboarding step 6(e),
docs/exchanges/ADDING_AN_EXCHANGE.md).

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md#BR-23,
`ExchangeAdapter.subscribe_ticker_stream` (abstract) +
`subscribe_order_stream` (L4-13 extension) in src/exchanges/common/adapter.py.

Connection/reconnect-loop details live in websocket_connection.py, push
message parsing in websocket_parsing.py (CLAUDE.md file-policy split,
ADR-2026-09-10-C) -- this module only wires the three
`ExchangeAdapter`-facing methods together.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from src.data.models.market_data import OrderBook
from src.data.models.trading import Order
from src.exchanges.common.types import TickerCallback
from src.exchanges.kiwoom.websocket_connection import (
    TYPE_ORDER_FILL,
    TYPE_ORDERBOOK,
    TYPE_STOCK_TRADE,
    WS_PAPER_URL,
    WS_REAL_URL,
    ConnectFn,
    KiwoomWsAuthClient,
    ReconnectHook,
    build_register_message,
    connect,
    run_kiwoom_ws_subscription,
)
from src.exchanges.kiwoom.websocket_parsing import (
    parse_order_fill_message,
    parse_orderbook_message,
    parse_stock_trade_message,
)

OrderBookCallback = Callable[[OrderBook], Awaitable[None]]
OrderCallback = Callable[[Order], Awaitable[None]]


class KiwoomWebSocketMixin:
    async def subscribe_ticker_stream(
        self: KiwoomWsAuthClient,
        symbol: str,
        callback: TickerCallback,
        *,
        on_reconnecting: ReconnectHook | None = None,
        on_reconnected: ReconnectHook | None = None,
        connect_fn: ConnectFn = connect,
    ) -> None:
        """`ExchangeAdapter` abstract method -- real-time trade (0B) channel."""
        token = await self._ensure_token()
        url = WS_PAPER_URL if self.is_paper_trading else WS_REAL_URL
        register_msg = build_register_message([symbol], TYPE_STOCK_TRADE)

        async def on_message(message: dict[str, Any]) -> None:
            for ticker in parse_stock_trade_message(message):
                await callback(ticker)

        await run_kiwoom_ws_subscription(
            url,
            token,
            register_msg,
            on_message,
            connect_fn=connect_fn,
            on_reconnecting=on_reconnecting,
            on_reconnected=on_reconnected,
        )

    async def subscribe_orderbook_stream(
        self: KiwoomWsAuthClient,
        symbol: str,
        callback: OrderBookCallback,
        *,
        on_reconnecting: ReconnectHook | None = None,
        on_reconnected: ReconnectHook | None = None,
        connect_fn: ConnectFn = connect,
    ) -> None:
        """Not part of the `ExchangeAdapter` ABC yet (same convention as
        KIS/Bitget's own extension streams -- stays Kiwoom-only until an
        FD-2 caller needs it) -- real-time order book depth (0D) channel."""
        token = await self._ensure_token()
        url = WS_PAPER_URL if self.is_paper_trading else WS_REAL_URL
        register_msg = build_register_message([symbol], TYPE_ORDERBOOK)

        async def on_message(message: dict[str, Any]) -> None:
            book = parse_orderbook_message(message)
            if book is not None:
                await callback(book)

        await run_kiwoom_ws_subscription(
            url,
            token,
            register_msg,
            on_message,
            connect_fn=connect_fn,
            on_reconnecting=on_reconnecting,
            on_reconnected=on_reconnected,
        )

    async def subscribe_order_stream(
        self: KiwoomWsAuthClient,
        callback: OrderCallback,
        *,
        on_reconnecting: ReconnectHook | None = None,
        on_reconnected: ReconnectHook | None = None,
        connect_fn: ConnectFn = connect,
    ) -> None:
        """`ExchangeAdapter`'s L4-13 extension (real-time order-fill, 00
        channel) -- `item` is account-wide scope, so an empty list per the
        official example's convention."""
        token = await self._ensure_token()
        url = WS_PAPER_URL if self.is_paper_trading else WS_REAL_URL
        register_msg = build_register_message([], TYPE_ORDER_FILL)

        async def on_message(message: dict[str, Any]) -> None:
            order = parse_order_fill_message(message)
            if order is not None:
                await callback(order)

        await run_kiwoom_ws_subscription(
            url,
            token,
            register_msg,
            on_message,
            connect_fn=connect_fn,
            on_reconnecting=on_reconnecting,
            on_reconnected=on_reconnected,
        )
