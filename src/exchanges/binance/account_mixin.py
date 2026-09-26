"""BR-22c -- BinanceAccountMixin: get_balance/get_positions/get_open_orders/
get_fills/get_order.

Spec: docs/exchanges/ADDING_AN_EXCHANGE.md step 1 (Binance), L4-13 조회 계약
(src/exchanges/common/adapter.py 모듈 docstring).

Endpoints (verified 2026-09-26 against the official Binance Spot API
documentation, github.com/binance/binance-spot-api-docs, `rest-api.md`
section "Account endpoints") -- all SIGNED (require API-KEY header +
`auth.py`'s query signature via `factory.py`'s `_request`):
- GET /api/v3/account -- account balances. Response `balances[]`: rows of
  `{asset, free, locked}`.
- GET /api/v3/openOrders -- open orders, optional `symbol` filter. Rows
  share the shape below.
- GET /api/v3/myTrades -- account trade history. `symbol` is a **required**
  parameter per the official spec (unlike `openOrders`) -- `get_fills`
  with no symbol raises rather than silently returning `[]` (the ABC's
  L4-13 contract: an empty list must mean "no fills," never "couldn't
  query," common/adapter.py module docstring).
- GET /api/v3/order -- single order status, `symbol` + `orderId`. Row
  fields used: `orderId`, `status` (NEW/PARTIALLY_FILLED/FILLED/CANCELED/
  REJECTED/EXPIRED), `side`, `type`, `origQty`, `executedQty`,
  `clientOrderId`.

Deviation: reuses `trading_mixin.py`'s `exchange_order_id` = "{symbol}:
{orderId}" composite convention (Binance's cancel/order-status endpoints
need `symbol` alongside the numeric `orderId`) -- `get_order()` parses that
same format so `modify_order()`'s call into `get_order()` (trading_mixin.py)
resolves correctly.

Binance spot has no native position concept (cash account, no margin/
leverage in this adapter's scope) -- `get_positions` always returns `[]`
(same convention as Bitget/KIS/NH/Kiwoom `account_mixin.py` for spot/cash
venues; actual holdings are queryable via `get_balance()`).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol
from uuid import uuid4

from src.core.exceptions import FatalExchangeError
from src.data.models.base import AssetClass
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
_MY_TRADES_PATH = "/api/v3/myTrades"
_ORDER_PATH = "/api/v3/order"

# Official docs, "Public API Definitions" §"Order status" -- REJECTED/EXPIRED
# not observed for orders this codebase itself submits (MARKET/LIMIT GTC)
# but mapped anyway since get_order()/get_open_orders() surface any order on
# the account, not just ones AIOS placed.
_STATUS_MAP: dict[str, OrderStatus] = {
    "NEW": OrderStatus.ACKNOWLEDGED,
    "PARTIALLY_FILLED": OrderStatus.PARTIALLY_FILLED,
    "FILLED": OrderStatus.FILLED,
    "CANCELED": OrderStatus.CANCELLED,
    "REJECTED": OrderStatus.REJECTED,
    "EXPIRED": OrderStatus.EXPIRED,
}


def _split_exchange_order_id(exchange_order_id: str) -> tuple[str, str]:
    if ":" not in exchange_order_id:
        raise FatalExchangeError(
            f"Binance exchange_order_id must be 'symbol:orderId' format: {exchange_order_id}"
        )
    symbol, order_id = exchange_order_id.split(":", 1)
    return symbol, order_id


def _order_from_raw(raw: dict[str, Any], *, symbol: str) -> Order:
    try:
        binance_order_id = raw["orderId"]
        side = OrderSide(raw["side"])
        order_type = OrderType(raw["type"])
        quantity = Decimal(str(raw["origQty"]))
        filled_quantity = Decimal(str(raw["executedQty"]))
        raw_status = raw["status"]
    except (KeyError, ValueError) as exc:
        raise FatalExchangeError(
            f"Binance order row missing/invalid expected field: {exc}"
        ) from exc
    status = _STATUS_MAP.get(raw_status, OrderStatus.UNKNOWN)
    return Order(
        order_id=uuid4(),
        exchange_order_id=f"{symbol}:{binance_order_id}",
        client_order_id=str(raw.get("clientOrderId", "")),
        strategy_id="",  # placeholder -- caller must merge with a DB row (same as KIS/Kiwoom)
        strategy_version="",
        symbol=symbol,
        exchange="binance",
        side=side,
        order_type=order_type,
        quantity=quantity,
        status=status,
        filled_quantity=filled_quantity,
        asset_class=AssetClass.CRYPTO,
    )


class _BinanceAccountClient(Protocol):
    async def _request(
        self, method: str, path: str, *, params: dict[str, Any] | None = None
    ) -> Any: ...


class BinanceAccountMixin:
    async def get_balance(
        self: _BinanceAccountClient, asset: str | None = None
    ) -> list[AccountBalance]:
        raw = await self._request("GET", _ACCOUNT_PATH)
        if not isinstance(raw, dict) or "balances" not in raw:
            raise FatalExchangeError(f"Binance account response missing balances: {raw!r}")
        balances: list[AccountBalance] = []
        try:
            for row in raw["balances"]:
                if asset is not None and row["asset"] != asset:
                    continue
                free = Decimal(str(row["free"]))
                locked = Decimal(str(row["locked"]))
                if free == 0 and locked == 0:
                    continue
                balances.append(
                    AccountBalance(
                        exchange="binance",
                        asset=row["asset"],
                        total=free + locked,
                        available=free,
                        used_margin=locked,
                    )
                )
        except KeyError as exc:
            raise FatalExchangeError(f"Binance balance row missing expected field: {exc}") from exc
        return balances

    async def get_positions(self, symbol: str | None = None) -> list[Position]:  # noqa: ARG002
        return []

    async def get_open_orders(
        self: _BinanceAccountClient, symbol: str | None = None
    ) -> list[Order]:
        params = {"symbol": symbol} if symbol is not None else None
        raw = await self._request("GET", _OPEN_ORDERS_PATH, params=params)
        if not isinstance(raw, list):
            raise FatalExchangeError(f"Binance open orders response is not a list: {raw!r}")
        return [_order_from_raw(row, symbol=row.get("symbol", symbol or "")) for row in raw]

    async def get_fills(
        self: _BinanceAccountClient,
        symbol: str | None = None,
        *,
        order_id: str | None = None,
        since: datetime | None = None,
    ) -> list[dict[str, Any]]:
        if symbol is None:
            raise FatalExchangeError(
                "Binance GET /api/v3/myTrades requires a symbol -- get_fills(symbol=None) "
                "cannot be queried on this venue (a real API constraint, not 'no fills')"
            )
        if since is not None and since.tzinfo is None:
            raise FatalExchangeError("Binance get_fills(since=) must be tz-aware UTC")
        params: dict[str, Any] = {"symbol": symbol}
        if order_id is not None:
            params["orderId"] = order_id
        if since is not None:
            params["startTime"] = str(int(since.timestamp() * 1000))
        raw = await self._request("GET", _MY_TRADES_PATH, params=params)
        if not isinstance(raw, list):
            raise FatalExchangeError(f"Binance myTrades response is not a list: {raw!r}")
        return raw

    async def get_order(self: _BinanceAccountClient, order_id: str) -> Order:
        symbol, binance_order_id = _split_exchange_order_id(order_id)
        raw = await self._request(
            "GET", _ORDER_PATH, params={"symbol": symbol, "orderId": binance_order_id}
        )
        if not isinstance(raw, dict):
            raise FatalExchangeError(f"Binance order response is not an object: {raw!r}")
        return _order_from_raw(raw, symbol=symbol)
