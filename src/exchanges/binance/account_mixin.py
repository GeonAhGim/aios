"""BR-22c -- BinanceAccountMixin: get_balance/get_positions/get_open_orders/get_fills/get_order.

Spec: docs/exchanges/ADDING_AN_EXCHANGE.md step 1,
      docs/audits/AUDIT_2026-09-26_order_path.md §3 F2(S), task-7997.

Endpoints (verified 2026-09-26 against the official Binance Spot API
documentation, github.com/binance/binance-spot-api-docs, `rest-api.md`):
- GET /api/v3/account -- account status and balances. Used fields:
  `balances[]`: `asset`, `free`, `locked`.
- GET /api/v3/openOrders -- all open orders. Same field shape as
  /api/v3/order (see ``row_to_order`` helper).
- GET /api/v3/myTrades -- filled trades for a symbol. Fields:
  `id` (trade id), `symbol`, `price`, `qty`, `commission`, `orderId`,
  `time` (ms epoch). `isBuyer` flag distinguishes side.
- GET /api/v3/order -- single order status lookup (existing).

Deviation: same "{symbol}:{orderId}" composite `exchange_order_id`
convention as `trading_mixin.py::place_order`/`cancel_order`/
`modify_order` (docstring there) -- this closes the loop
`trading_mixin.BinanceTradingMixin`'s `_OrderMutatingClient` Protocol
already declared (`modify_order` calling `self.get_order(...)` after a
cancelReplace) but had no implementation for until now.

get_positions(): Binance spot (cash mode) has no per-symbol position
concept -- holdings live in balances instead. Returns [] (empty list)
rather than raising ``UnsupportedCapabilityError``, because spot
positions are genuinely empty (not "we don't know how to query them").
This matches OKX's spot-mode convention
(`okx/account_mixin.py` docstring).

Every field this method trusts comes from the exchange's own response;
AIOS-only context (strategy_id/strategy_version/asset_class) is left as a
placeholder, same convention as Bitget/KIS/Kiwoom account_mixin.py --
callers merge this with their own DB row.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol

from src.core.exceptions import FatalExchangeError
from src.data.models.base import AssetClass, Currency, Money
from src.data.models.trading import (
    AccountBalance,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
)

_ACCOUNT_PATH = "/api/v3/account"
_OPEN_ORDERS_PATH = "/api/v3/openOrders"
_FILLS_PATH = "/api/v3/myTrades"
_ORDER_PATH = "/api/v3/order"
_QUOTE_CURRENCIES = ("USDT",)  # Phase 1 scope (bitget/kis account_mixin.py precedent)

_STATUS_MAP = {
    "NEW": OrderStatus.ACKNOWLEDGED,
    "PENDING_NEW": OrderStatus.ACKNOWLEDGED,
    "PARTIALLY_FILLED": OrderStatus.PARTIALLY_FILLED,
    "FILLED": OrderStatus.FILLED,
    "CANCELED": OrderStatus.CANCELLED,
    "PENDING_CANCEL": OrderStatus.CANCEL_REQUESTED,
    "REJECTED": OrderStatus.REJECTED,
    "EXPIRED": OrderStatus.EXPIRED,
    "EXPIRED_IN_MATCH": OrderStatus.EXPIRED,
}

_ORDER_TYPE_MAP = {
    "LIMIT": OrderType.LIMIT,
    "MARKET": OrderType.MARKET,
}


def _map_status(raw_status: str) -> OrderStatus:
    return _STATUS_MAP.get(raw_status, OrderStatus.UNKNOWN)


def _map_order_type(raw_type: str) -> OrderType:
    """Fail-closed -- our domain `OrderType` only models MARKET/LIMIT
    (same posture as `okx/trading_mixin.py::_to_okx_ord_type`). Binance
    spot can return other types (STOP_LOSS, TAKE_PROFIT_LIMIT, ...) for
    orders this adapter never placed itself; guessing a mapping for those
    would misrepresent the order rather than surfacing the gap."""
    try:
        return _ORDER_TYPE_MAP[raw_type]
    except KeyError:
        raise FatalExchangeError(
            f"Binance order type not representable in domain model: {raw_type!r}"
        ) from None


def _split_exchange_order_id(exchange_order_id: str) -> tuple[str, str]:
    if ":" not in exchange_order_id:
        raise FatalExchangeError(
            f"Binance exchange_order_id must be 'symbol:orderId' format: {exchange_order_id}"
        )
    symbol, order_id = exchange_order_id.split(":", 1)
    return symbol, order_id


def _parse_epoch_ms(raw_ms: Any) -> datetime:
    return datetime.fromtimestamp(int(raw_ms) / 1000, tz=UTC)


def _average_fill_price(symbol: str, data: dict[str, Any]) -> Money | None:
    """`cummulativeQuoteQty / executedQty` is Binance's own average fill
    price. Returns None when executedQty is zero (order not yet filled) or
    when the symbol is not a known quote currency (USDT phase-1 scope) --
    the caller can fill currency from context."""
    if not symbol.endswith(_QUOTE_CURRENCIES):
        return None
    executed_qty = Decimal(str(data.get("executedQty", "0")))
    if executed_qty == 0:
        return None
    cumulative_quote = Decimal(str(data.get("cummulativeQuoteQty", "0")))
    return Money(amount=cumulative_quote / executed_qty, currency=Currency.USDT)


def _row_to_order(row: dict[str, Any]) -> Order:
    """Convert a raw Binance order row (from get_order or get_open_orders)
    into a domain ``Order``."""
    symbol = row["symbol"]
    try:
        status = _map_status(row["status"])
        order_type = _map_order_type(row["type"])
        side = OrderSide(row["side"])
    except KeyError as exc:
        raise FatalExchangeError(f"Binance order row missing expected field: {exc}") from exc
    except ValueError:
        raise FatalExchangeError(
            f"Binance order row has unrecognized side: {row['side']!r}"
        ) from None

    return Order(
        exchange_order_id=f"{symbol}:{row.get('orderId', '')}",
        client_order_id=row.get("clientOrderId", ""),
        strategy_id="",  # placeholder -- caller must fill from a DB lookup
        strategy_version="",
        symbol=symbol,
        exchange="binance",
        side=side,
        order_type=order_type,
        quantity=Decimal(str(row["origQty"])),
        status=status,
        filled_quantity=Decimal(str(row["executedQty"])),
        average_fill_price=_average_fill_price(symbol, row),
        created_at=_parse_epoch_ms(row["time"]),
        updated_at=_parse_epoch_ms(row.get("updateTime", row["time"])),
        asset_class=AssetClass.CRYPTO,
    )


class _BinanceAccountClient(Protocol):
    """Minimal HTTP contract needed at mixin-assembly time -- same
    reasoning as `trading_mixin.py`'s local `_BinanceOrderClient` (the
    concrete signed-request client for Binance is not yet registered in
    `src/exchanges/common/http_client.py`)."""

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...


class BinanceAccountMixin:
    async def get_balance(
        self: _BinanceAccountClient,
        asset: str | None = None,
    ) -> list[AccountBalance]:
        raw = await self._request("GET", _ACCOUNT_PATH)
        balances: list[dict[str, Any]] = raw.get("balances", [])
        result: list[AccountBalance] = []
        for item in balances:
            free = Decimal(item["free"])
            locked = Decimal(item["locked"])
            if free == 0 and locked == 0:
                continue  # skip zero-balance assets
            if asset is not None and item["asset"] != asset:
                continue
            result.append(
                AccountBalance(
                    exchange="binance",
                    asset=item["asset"],
                    total=free + locked,
                    available=free,
                    used_margin=locked,
                )
            )
        return result

    async def get_positions(
        self: _BinanceAccountClient,
        symbol: str | None = None,  # noqa: ARG002
    ) -> list[Position]:
        """Binance spot (cash mode) has no per-symbol position concept.
        Holdings are tracked via ``get_balance()`` instead. Returns [] --
        this is a factual empty set, not an unsupported operation."""
        return []

    async def get_open_orders(
        self: _BinanceAccountClient,
        symbol: str | None = None,
    ) -> list[Order]:
        params: dict[str, Any] | None = {"symbol": symbol} if symbol else None
        raw = await self._request("GET", _OPEN_ORDERS_PATH, params=params)
        if isinstance(raw, list):
            rows = raw
        elif isinstance(raw, dict):
            rows = raw.get("data", [])
        else:
            rows = []
        return [_row_to_order(row) for row in rows]

    async def get_fills(
        self: _BinanceAccountClient,
        symbol: str | None = None,
        *,
        order_id: str | None = None,
        since: datetime | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {}
        if symbol is not None:
            params["symbol"] = symbol
        if order_id is not None:
            params["orderId"] = order_id
        if since is not None:
            params["startTime"] = int(since.timestamp() * 1000)
        raw = await self._request("GET", _FILLS_PATH, params=params or None)
        rows: list[dict[str, Any]] = raw.get("data", raw)
        if isinstance(raw, list):
            rows = raw
        if isinstance(rows, dict):
            rows = rows.get("data", [])
        return list(rows)

    async def get_order(self: _BinanceAccountClient, order_id: str) -> Order:
        symbol, binance_order_id = _split_exchange_order_id(order_id)
        raw = await self._request(
            "GET",
            _ORDER_PATH,
            params={"symbol": symbol, "orderId": binance_order_id},
        )
        try:
            status = _map_status(raw["status"])
            order_type = _map_order_type(raw["type"])
            side = OrderSide(raw["side"])
            quantity = Decimal(str(raw["origQty"]))
            filled_quantity = Decimal(str(raw["executedQty"]))
            created_at = _parse_epoch_ms(raw["time"])
            updated_at = _parse_epoch_ms(raw["updateTime"])
        except KeyError as exc:
            raise FatalExchangeError(
                f"Binance order response missing expected field: {exc}"
            ) from exc

        return Order(
            exchange_order_id=f"{symbol}:{raw.get('orderId', binance_order_id)}",
            client_order_id=raw.get("clientOrderId", ""),
            strategy_id="",  # placeholder -- caller must fill from a DB lookup
            strategy_version="",
            symbol=symbol,
            exchange="binance",
            side=side,
            order_type=order_type,
            quantity=quantity,
            status=status,
            filled_quantity=filled_quantity,
            average_fill_price=_average_fill_price(symbol, raw),
            created_at=created_at,
            updated_at=updated_at,
            asset_class=AssetClass.CRYPTO,
        )
