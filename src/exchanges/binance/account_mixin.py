"""BR-22c -- BinanceAccountMixin: get_order.

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
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Protocol

from src.core.exceptions import FatalExchangeError
from src.data.models.base import AssetClass, Currency, Money
from src.data.models.trading import Order, OrderSide, OrderStatus, OrderType

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
    return datetime.fromtimestamp(int(raw_ms) / 1000, tz=timezone.utc)


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
    ) -> dict[str, Any]: ...


class BinanceAccountMixin:
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
