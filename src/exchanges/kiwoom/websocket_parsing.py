# ratchet-allow: two field ids on the "0B" real-time trade push (27=best ask
# price, 28=best bid price) and the "00" real-time order-fill side code
# (907) convention ("1"=SELL, "2"=BUY) are carried over from the legacy
# Kiwoom OpenAPI+ FID table (Kiwoom's REST API migration docs say real-time
# FIDs reuse that numbering) but were not independently re-confirmed against
# the example source fetched in this session -- see module docstring for
# exactly what was vs wasn't confirmed.
"""BR-23(task-7572) -- Kiwoom WebSocket push-message parsing (0B/0D/00).

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md#BR-23.
Split out of websocket.py per CLAUDE.md file-policy (ADR-2026-09-10-C).

Verified 2026-09-26 (WebFetch, github.com/Kiwoom-Securities/Kiwoom-REST-API,
`examples/domestic_stock/realtime/subscribe_domestic_{stock_trade,
stock_order_book_depth,order_fill}_async.py`, official Kiwoom Securities
REST API client repo -- same source cited by trading_mixin.py, task-7571):
- Push envelope: `{"trnm": "REAL", "data": [{"type": ..., "item": ...,
  "values": {<field id>: <string value>, ...}}]}` (matches the legacy
  Kiwoom OpenAPI+ FID-keyed push shape that the REST API's real-time channel
  is documented to preserve).
- "0D" field ids confirmed directly: best ask price/qty level 1 = 41/61,
  best bid price/qty level 1 = 51/71 (10-level pattern: ask price 41-50,
  ask qty 61-70, bid price 51-60, bid qty 71-80).
- "00" field ids confirmed directly: 9203=order number, 9001=symbol,
  907=side code, 913=order status text, 900=order quantity,
  911=filled quantity, 901=order price, 910=fill price.
- "0B" field ids confirmed directly: 10=price, 15=volume, 16/17/18=open/
  high/low, 20=trade time. 27/28 (best ask/bid) are the ratchet-noted
  carry-over above.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from src.core.exceptions import FatalExchangeError
from src.data.models.base import AssetClass
from src.data.models.market_data import OrderBook, OrderBookLevel, Ticker
from src.data.models.trading import Order, OrderSide, OrderStatus, OrderType
from src.exchanges.kiwoom.websocket_connection import (
    TYPE_ORDER_FILL,
    TYPE_ORDERBOOK,
    TYPE_STOCK_TRADE,
)


def real_entries(message: dict[str, Any], type_code: str) -> list[dict[str, Any]]:
    if message.get("trnm") != "REAL":
        return []
    return [entry for entry in message.get("data", []) if entry.get("type") == type_code]


def parse_stock_trade_message(message: dict[str, Any]) -> list[Ticker]:
    tickers = []
    for entry in real_entries(message, TYPE_STOCK_TRADE):
        values = entry.get("values", {})
        try:
            tickers.append(
                Ticker(
                    symbol=str(entry.get("item", "")),
                    exchange="kiwoom",
                    price=Decimal(values["10"]),
                    bid=Decimal(values["28"]),
                    ask=Decimal(values["27"]),
                    volume_24h=Decimal(values["15"]),
                    timestamp=datetime.now(timezone.utc),
                    source_type="primary",
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise FatalExchangeError(
                f"Kiwoom real-time trade message missing expected field: {exc}"
            ) from exc
    return tickers


def parse_orderbook_message(message: dict[str, Any]) -> OrderBook | None:
    entries = real_entries(message, TYPE_ORDERBOOK)
    if not entries:
        return None
    entry = entries[0]
    values = entry.get("values", {})
    try:
        bids = [
            OrderBookLevel(
                price=Decimal(values[str(50 + i)]), quantity=Decimal(values[str(70 + i)])
            )
            for i in range(1, 11)
            if values.get(str(50 + i))
        ]
        asks = [
            OrderBookLevel(
                price=Decimal(values[str(40 + i)]), quantity=Decimal(values[str(60 + i)])
            )
            for i in range(1, 11)
            if values.get(str(40 + i))
        ]
    except (KeyError, TypeError, ValueError) as exc:
        raise FatalExchangeError(
            f"Kiwoom real-time orderbook message missing expected field: {exc}"
        ) from exc
    return OrderBook(
        symbol=str(entry.get("item", "")),
        exchange="kiwoom",
        bids=bids,
        asks=asks,
        timestamp=datetime.now(timezone.utc),
    )


def parse_order_fill_message(message: dict[str, Any]) -> Order | None:
    entries = real_entries(message, TYPE_ORDER_FILL)
    if not entries:
        return None
    values = entries[0].get("values", {})
    try:
        order_no = values["9203"]
        symbol = values["9001"]
        side_code = values["907"]
        order_status_raw = values["913"]
        order_qty = Decimal(values.get("900", "0") or "0")
        filled_qty = Decimal(values.get("911", "0") or "0")
    except (KeyError, TypeError) as exc:
        raise FatalExchangeError(
            f"Kiwoom real-time order-fill message missing expected field: {exc}"
        ) from exc

    if "체결" in order_status_raw:
        status = OrderStatus.FILLED if filled_qty >= order_qty > 0 else OrderStatus.PARTIALLY_FILLED
    else:
        status = OrderStatus.ACKNOWLEDGED

    return Order(
        exchange_order_id=order_no,
        client_order_id="",
        strategy_id="",
        strategy_version="",
        symbol=symbol,
        exchange="kiwoom",
        side=OrderSide.SELL if side_code == "1" else OrderSide.BUY,
        # Kiwoom's push doesn't distinguish LIMIT/MARKET on this channel --
        # same safe-default reasoning as KIS's parse_order_notification_message.
        order_type=OrderType.LIMIT,
        quantity=order_qty,
        status=status,
        filled_quantity=filled_qty,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        asset_class=AssetClass.KR_EQUITY,
    )
