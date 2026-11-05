"""BR-22c -- BinanceAccountMixin: get_order/get_balance/get_positions/get_open_orders/get_fills.

Spec: docs/audits/AUDIT_2026-09-26_order_path.md §3 F2(S), task-7997.

Endpoint (verified 2026-09-26 against the official Binance Spot API
documentation, github.com/binance/binance-spot-api-docs, `rest-api.md`
§"Query order (USER_DATA)"):
- GET /api/v3/order -- required: symbol, orderId. Response fields used
  here: `symbol`, `orderId`, `clientOrderId`, `price`, `origQty`,
  `executedQty`, `cummulativeQuoteQty`, `status`, `type`, `side`, `time`
  (creation, ms epoch), `updateTime` (ms epoch). `status` enumerates NEW /
  PENDING_NEW / PARTIALLY_FILLED / FILLED / CANCELED / PENDING_CANCEL /
  REJECTED / EXPIRED / EXPIRED_IN_MATCH -- all mapped below, unmapped
  values fall back to UNKNOWN (8.3 principle: an unrecognized value is not
  assumed to mean failure).

Deviation: same "{symbol}:{orderId}" composite `exchange_order_id`
convention as `trading_mixin.py::place_order`/`cancel_order`/
`modify_order` (docstring there) -- this closes the loop
`trading_mixin.BinanceTradingMixin`'s `_OrderMutatingClient` Protocol
already declared (`modify_order` calling `self.get_order(...)` after a
cancelReplace) but had no implementation for until now. Before this leaf,
Binance had no way to resolve an UNKNOWN order (unknown_resolver), recover
state after a restart (restart_recovery), or reverse-lookup by clientOid
-- all three require this method to exist (task-7978 F2 finding).

Every field this method trusts comes from the exchange's own response;
AIOS-only context (strategy_id/strategy_version/asset_class) is left as a
placeholder, same convention as Bitget/KIS/Kiwoom account_mixin.py --
callers merge this with their own DB row.

Added in task-22260 (endpoints UNVERIFIED against live responses -- tested
only with fake HTTP payloads shaped per the public Spot API docs):
- get_balance: GET /api/v3/account `balances[] {asset, free, locked}`.
- get_open_orders: GET /api/v3/openOrders (optional `symbol`).
- get_fills: GET /api/v3/myTrades (`symbol` REQUIRED by Binance; `orderId`,
  `startTime` ms optional). Returns raw rows, no normalization here.
- get_positions: spot has no native positions; synthesized from balances
  (entry price = current price placeholder, pnl 0), Bitget precedent.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol

from src.core.exceptions import ExchangeAPIError, FatalExchangeError
from src.data.models.base import AssetClass, Currency, Money
from src.data.models.trading import (
    AccountBalance,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
)
from src.exchanges.binance.symbols import to_binance_symbol

_ORDER_PATH = "/api/v3/order"
_ACCOUNT_PATH = "/api/v3/account"
_OPEN_ORDERS_PATH = "/api/v3/openOrders"
_MY_TRADES_PATH = "/api/v3/myTrades"
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
    """`cummulativeQuoteQty / executedQty` is Binance's own documented way
    to derive the average fill price (no separate `avgPrice` field on this
    endpoint). Only computed for USDT-quoted symbols (Phase 1 scope,
    `_QUOTE_CURRENCIES`) since `Money` has no currency for other quotes
    yet -- other symbols keep this `None` rather than mislabel the
    currency."""
    if not symbol.endswith(_QUOTE_CURRENCIES):
        return None
    executed_qty = Decimal(str(data.get("executedQty", "0")))
    if executed_qty == 0:
        return None
    cumulative_quote = Decimal(str(data.get("cummulativeQuoteQty", "0")))
    return Money(amount=cumulative_quote / executed_qty, currency=Currency.USDT)


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
    ) -> Any: ...


def _order_from_raw(raw: dict[str, Any], symbol: str, binance_order_id: str) -> Order:
    try:
        status = _map_status(raw["status"])
        order_type = _map_order_type(raw["type"])
        side = OrderSide(raw["side"])
        quantity = Decimal(str(raw["origQty"]))
        filled_quantity = Decimal(str(raw["executedQty"]))
        created_at = _parse_epoch_ms(raw["time"])
        updated_at = _parse_epoch_ms(raw["updateTime"])
    except KeyError as exc:
        raise FatalExchangeError(f"Binance order response missing expected field: {exc}") from exc

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


_QUOTE_ASSET = "USDT"


class BinanceAccountMixin:
    async def get_order(self: _BinanceAccountClient, order_id: str) -> Order:
        symbol, binance_order_id = _split_exchange_order_id(order_id)
        raw = await self._request(
            "GET",
            _ORDER_PATH,
            params={"symbol": symbol, "orderId": binance_order_id},
        )
        return _order_from_raw(raw, symbol, binance_order_id)

    async def get_balance(
        self: _BinanceAccountClient, asset: str | None = None
    ) -> list[AccountBalance]:
        raw = await self._request("GET", _ACCOUNT_PATH)
        try:
            rows = raw["balances"]
            now = datetime.now(UTC)
            result = []
            for row in rows:
                free = Decimal(str(row["free"]))
                total = free + Decimal(str(row["locked"]))
                if asset is None and total == 0:
                    continue
                if asset is not None and row["asset"] != asset:
                    continue
                result.append(
                    AccountBalance(
                        exchange="binance",
                        asset=row["asset"],
                        total=total,
                        available=free,
                        updated_at=now,
                    )
                )
        except (KeyError, TypeError) as exc:
            raise FatalExchangeError(f"Binance account response malformed: {exc!r}") from exc
        return result

    async def get_positions(self: Any, symbol: str | None = None) -> list[Position]:
        asset_filter = symbol.split("/")[0] if symbol else None
        now = datetime.now(UTC)
        zero = Money(amount=Decimal("0"), currency=Currency.USDT)
        positions = []
        for balance in await self.get_balance(asset_filter):
            if balance.asset == _QUOTE_ASSET or balance.total == 0:
                continue
            pair = f"{balance.asset}/{_QUOTE_ASSET}"
            try:
                ticker = await self.get_ticker(pair)
            except ExchangeAPIError:
                continue  # one unpriced coin must not fail the whole query
            price = Money(amount=ticker.price, currency=Currency.USDT)
            positions.append(
                Position(
                    symbol=pair,
                    exchange="binance",
                    strategy_id="",  # placeholder -- caller fills from DB
                    quantity=balance.total,
                    average_entry_price=price,  # venue does not track cost basis
                    current_price=price,
                    unrealized_pnl=zero,
                    realized_pnl=zero,
                    entry_time=now,
                    updated_at=now,
                    asset_class=AssetClass.CRYPTO,
                )
            )
        return positions

    async def get_open_orders(
        self: _BinanceAccountClient, symbol: str | None = None
    ) -> list[Order]:
        params = {"symbol": to_binance_symbol(symbol)} if symbol else None
        raw = await self._request("GET", _OPEN_ORDERS_PATH, params=params)
        if not isinstance(raw, list):
            raise FatalExchangeError("Binance openOrders response is not a list")
        try:
            return [_order_from_raw(row, row["symbol"], str(row["orderId"])) for row in raw]
        except KeyError as exc:
            raise FatalExchangeError(
                f"Binance openOrders row missing expected field: {exc}"
            ) from exc

    async def get_fills(
        self: _BinanceAccountClient,
        symbol: str | None = None,
        *,
        order_id: str | None = None,
        since: datetime | None = None,
    ) -> list[dict[str, Any]]:
        if symbol is None:
            raise FatalExchangeError("Binance myTrades requires a symbol")
        params: dict[str, Any] = {"symbol": to_binance_symbol(symbol)}
        if order_id is not None:
            params["orderId"] = order_id
        if since is not None:
            if since.tzinfo is None:
                raise FatalExchangeError("get_fills `since` must be timezone-aware UTC")
            params["startTime"] = int(since.timestamp() * 1000)
        raw = await self._request("GET", _MY_TRADES_PATH, params=params)
        if not isinstance(raw, list):
            raise FatalExchangeError("Binance myTrades response is not a list")
        return raw
