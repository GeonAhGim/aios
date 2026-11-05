"""BR-22e -- Binance WebSocket frame parsers (pure, no socket).

Message shapes (DOC_ONLY / unverified live -- Binance Spot `web-socket-streams.md`
and `user-data-stream.md`):
- Public `<symbol>@ticker`: `{"e":"24hrTicker","E":ms,"s":"BTCUSDT","c":last,
  "b":bid,"a":ask,"v":24h base volume}`; the combined-stream wrapper
  `{"stream":..., "data":{...}}` is unwrapped.
- Public partial depth `<symbol>@depth<N>`: `{"lastUpdateId":n,"bids":[[p,q]],
  "asks":[[p,q]]}` (no symbol in the body -- the caller passes it). Each frame
  is treated as a full snapshot.
- Private user data stream (listenKey): `executionReport` with `s` symbol,
  `c` clientOrderId, `S` side, `o` type, `q` qty, `p` price, `X` status,
  `i` orderId, `z` cumulative filled qty, `Z` cumulative quote qty,
  `E` event time (ms). `listenKeyExpired` is surfaced as an error because the
  stream is dead until a new listenKey is created.

Any frame that does not match raises `FatalExchangeError` -- never a silent skip.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from src.core.exceptions import FatalExchangeError
from src.data.models.base import AssetClass, Currency, Money
from src.data.models.market_data import OrderBook, OrderBookLevel, Ticker
from src.data.models.trading import Order, OrderSide, OrderStatus, OrderType
from src.exchanges.binance.symbols import from_binance_symbol

# Binance executionReport `X` -> AIOS status. Unknown values map to UNKNOWN
# (8.3: UNKNOWN does not mean failure), not to a guessed terminal state.
_STATUS_MAP: dict[str, OrderStatus] = {
    "NEW": OrderStatus.ACKNOWLEDGED,
    "PARTIALLY_FILLED": OrderStatus.PARTIALLY_FILLED,
    "FILLED": OrderStatus.FILLED,
    "CANCELED": OrderStatus.CANCELLED,
    "REJECTED": OrderStatus.REJECTED,
    "EXPIRED": OrderStatus.EXPIRED,
}


def _unwrap(message: Any) -> dict[str, Any]:
    if not isinstance(message, dict):
        raise FatalExchangeError(f"Binance WS frame is not an object: {message!r}")
    inner = message.get("data")
    if "stream" in message and isinstance(inner, dict):
        return inner
    return message


def _utc_from_ms(value: Any) -> datetime:
    return datetime.fromtimestamp(int(value) / 1000, tz=UTC)


def parse_ticker_ws_message(message: Any) -> Ticker:
    raw = _unwrap(message)
    if raw.get("e") != "24hrTicker":
        raise FatalExchangeError(f"Binance WS frame is not a 24hrTicker: {raw!r}")
    try:
        last = Decimal(str(raw["c"]))
        return Ticker(
            symbol=from_binance_symbol(raw["s"]),
            exchange="binance",
            price=last,
            bid=Decimal(str(raw.get("b") or raw["c"])),
            ask=Decimal(str(raw.get("a") or raw["c"])),
            volume_24h=Decimal(str(raw.get("v", "0"))),
            timestamp=_utc_from_ms(raw["E"]),
            source_type="primary",
        )
    except (KeyError, TypeError, InvalidOperation, ValueError) as exc:
        raise FatalExchangeError(f"Binance WS ticker malformed: {exc}, raw={raw!r}") from exc


def parse_orderbook_ws_message(message: Any, *, symbol: str) -> OrderBook:
    raw = _unwrap(message)
    try:
        bids = [
            OrderBookLevel(price=Decimal(str(lv[0])), quantity=Decimal(str(lv[1])))
            for lv in raw["bids"]
        ]
        asks = [
            OrderBookLevel(price=Decimal(str(lv[0])), quantity=Decimal(str(lv[1])))
            for lv in raw["asks"]
        ]
    except (KeyError, IndexError, TypeError, InvalidOperation, ValueError) as exc:
        raise FatalExchangeError(f"Binance WS depth malformed: {exc}, raw={raw!r}") from exc
    return OrderBook(
        symbol=symbol,
        exchange="binance",
        bids=bids,
        asks=asks,
        timestamp=datetime.now(UTC),
    )


def parse_order_ws_message(message: Any) -> Order:
    """Parse a user-data `executionReport` into an `Order`.

    strategy_id/strategy_version are "unknown" -- the exchange does not carry
    them; the caller correlates by `client_order_id`.
    """
    raw = _unwrap(message)
    event = raw.get("e")
    if event == "listenKeyExpired":
        raise FatalExchangeError("Binance listenKey expired; user data stream must be recreated")
    if event != "executionReport":
        raise FatalExchangeError(f"Binance WS frame is not an executionReport: {raw!r}")
    try:
        filled = Decimal(str(raw["z"]))
        quote = Decimal(str(raw["Z"]))
        price = Decimal(str(raw["p"]))
        order_type = OrderType(raw["o"])
        symbol = from_binance_symbol(raw["s"])
        currency = Currency(symbol.split("/")[1])
        return Order(
            exchange_order_id=f"{raw['s']}:{raw['i']}",
            client_order_id=str(raw["c"]),
            strategy_id="unknown",
            strategy_version="unknown",
            symbol=symbol,
            exchange="binance",
            side=OrderSide(raw["S"]),
            order_type=order_type,
            quantity=Decimal(str(raw["q"])),
            price=Money(amount=price, currency=currency) if order_type == OrderType.LIMIT else None,
            status=_STATUS_MAP.get(raw["X"], OrderStatus.UNKNOWN),
            filled_quantity=filled,
            average_fill_price=(
                Money(amount=quote / filled, currency=currency) if filled > 0 else None
            ),
            updated_at=_utc_from_ms(raw["E"]),
            asset_class=AssetClass.CRYPTO,
        )
    except (KeyError, TypeError, InvalidOperation, ValueError) as exc:
        raise FatalExchangeError(f"Binance WS order malformed: {exc}, raw={raw!r}") from exc
