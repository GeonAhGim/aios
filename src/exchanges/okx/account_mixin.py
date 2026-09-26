"""BR-21c -- OKXAccountMixin: get_balance/get_positions/get_open_orders/get_fills/get_order.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md#BR-21,
docs/exchanges/ADDING_AN_EXCHANGE.md step 1.

Endpoints (verified 2026-09-26 against the official OKX v5 API reference,
www.okx.com/docs-v5/en, "Account"/"Order Book Trading > Trade" sections):
- GET /api/v5/account/balance(?ccy=) -- `data[0].details[]`: `ccy`, `bal`
  (total equity), `availBal` (available), `frozenBal` (locked).
- GET /api/v5/trade/orders-pending(?instId=) -- open orders, one row per
  order, same field shape as `get_order`'s row (see `row_to_order`).
- GET /api/v5/trade/fills(?instId=&ordId=&begin=) -- per-fill rows:
  `instId`, `tradeId`, `ordId`, `fillPx`, `fillSz`, `side`, `ts`, `fee`,
  `feeCcy`. `begin` is a ms-epoch lower bound (exclusive per docs).
- GET /api/v5/trade/order?instId=&ordId= -- single order status lookup.

Deviation: `okx/trading_mixin.py::place_order` synthesizes
`exchange_order_id` as "{instId}:{ordId}" because OKX's cancel/amend/query
endpoints all need `instId` alongside `ordId` (same composite-id
convention as KIS/Kiwoom, documented in that module's docstring) --
`get_order()`/`modify_order()`'s callback here expect and parse that same
format.

get_positions(): OKX's `/api/v5/account/positions` endpoint only returns
rows for margin/futures/swap/options instruments -- a spot (`tdMode=cash`)
position has no such row (spot holdings live in `get_balance()` instead).
Since this leaf's scope is exactly the spot/cash trade mode
(`okx/trading_mixin.py` module docstring), `get_positions()` explicitly
raises `UnsupportedCapabilityError` via `self._unsupported(...)` instead of
returning an empty list -- an empty list would read as "you asked and
there truly are zero positions," which is a different (and false) claim
from "this adapter's scope doesn't have a positions concept yet"
(ADDING_AN_EXCHANGE.md step 1's silent-empty-value warning).
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol
from uuid import uuid4

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
from src.exchanges.common.adapter import UnsupportedCapabilityError

_BALANCE_PATH = "/api/v5/account/balance"
_OPEN_ORDERS_PATH = "/api/v5/trade/orders-pending"
_FILLS_PATH = "/api/v5/trade/fills"
_ORDER_PATH = "/api/v5/trade/order"

# Confirmed values from the official order-state enum (docs, "Order Book
# Trading > Trade > GET / Order List"). Anything outside this set maps to
# UNKNOWN rather than guessing (same fail-closed fallback as
# bitget/trading_query_mixin.py's `_STATUS_MAP.get(status, UNKNOWN)`).
_STATUS_MAP = {
    "live": OrderStatus.ACKNOWLEDGED,
    "partially_filled": OrderStatus.PARTIALLY_FILLED,
    "filled": OrderStatus.FILLED,
    "canceled": OrderStatus.CANCELLED,
}


class _OKXAccountClient(Protocol):
    """Minimal HTTP contract needed at mixin-assembly time -- same local
    declaration reasoning as `okx/trading_mixin.py::_OKXOrderClient`."""

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...


def _split_exchange_order_id(exchange_order_id: str) -> tuple[str, str]:
    """Mirrors `okx/trading_mixin.py::_split_exchange_order_id` -- kept as a
    local copy rather than a cross-module import of a private helper, same
    precedent as `kiwoom/account_mixin.py` duplicating its trading_mixin's
    composite-id splitter."""
    if ":" not in exchange_order_id:
        raise FatalExchangeError(
            f"OKX exchange_order_id must be 'instId:ordId' format: {exchange_order_id}"
        )
    inst_id, ord_id = exchange_order_id.split(":", 1)
    return inst_id, ord_id


def _map_status(raw_status: str) -> OrderStatus:
    return _STATUS_MAP.get(raw_status, OrderStatus.UNKNOWN)


def _to_epoch_ms(since: datetime) -> str:
    """tz-aware only (fail-closed) -- a naive datetime silently interpreted
    as UTC or local time would misalign the fills window by a timezone
    offset (same rule as bitget/trading_query_mixin.py::_to_epoch_ms)."""
    if since.tzinfo is None or since.utcoffset() is None:
        raise ValueError("get_fills(since=) requires a tz-aware datetime (naive is rejected).")
    return str(int(since.timestamp() * 1000))


def row_to_order(row: dict[str, Any]) -> Order:
    """`average_fill_price` is priced in the quote currency of `instId`
    (e.g. "BTC-USDT" -> USDT) -- this leaf's spot scope is USDT-quoted
    pairs only (same Phase 1 restriction as `bitget/account_mixin.py`'s
    `_QUOTE_CURRENCIES`), so `Currency.USDT` is used unconditionally."""
    try:
        inst_id = row["instId"]
        ord_id = row["ordId"]
        status = _map_status(row.get("state", ""))
        quantity = Decimal(str(row["sz"]))
        filled_quantity = Decimal(str(row.get("accFillSz", "0")))
        side = OrderSide(str(row["side"]).upper())
        order_type = OrderType.LIMIT if row.get("ordType") != "market" else OrderType.MARKET
        avg_px_raw = row.get("avgPx")
        average_fill_price = (
            Money(amount=Decimal(str(avg_px_raw)), currency=Currency.USDT)
            if avg_px_raw not in (None, "", "0")
            else None
        )
    except (KeyError, InvalidOperation, ValueError) as exc:
        raise FatalExchangeError(
            f"OKX order row missing/invalid field (instId/ordId/sz/side required): "
            f"{exc}, row={row!r}"
        ) from exc
    return Order(
        order_id=uuid4(),
        exchange_order_id=f"{inst_id}:{ord_id}",
        client_order_id=row.get("clOrdId", ""),
        strategy_id="",  # placeholder -- caller must merge with a DB row
        strategy_version="",
        symbol=inst_id,
        exchange="okx",
        side=side,
        order_type=order_type,
        quantity=quantity,
        status=status,
        filled_quantity=filled_quantity,
        average_fill_price=average_fill_price,
        created_at=_epoch_ms_to_datetime(row.get("cTime")),
        updated_at=_epoch_ms_to_datetime(row.get("uTime")),
        asset_class=AssetClass.CRYPTO,
    )


def _epoch_ms_to_datetime(raw: str | None) -> datetime:
    if not raw:
        return datetime.now(timezone.utc)
    return datetime.fromtimestamp(int(raw) / 1000, tz=timezone.utc)


class OKXAccountMixin:
    async def get_balance(
        self: _OKXAccountClient, asset: str | None = None
    ) -> list[AccountBalance]:
        params = {"ccy": asset} if asset is not None else None
        raw = await self._request("GET", _BALANCE_PATH, params=params)
        data = raw.get("data")
        if not data:
            raise FatalExchangeError(f"OKX balance response has an empty data array: {raw!r}")
        details = data[0].get("details")
        if details is None:
            raise FatalExchangeError(f"OKX balance response missing details array: {raw!r}")
        balances: list[AccountBalance] = []
        try:
            for row in details:
                balances.append(
                    AccountBalance(
                        exchange="okx",
                        asset=row["ccy"],
                        total=Decimal(str(row["bal"])),
                        available=Decimal(str(row["availBal"])),
                        used_margin=Decimal(str(row.get("frozenBal", "0"))),
                    )
                )
        except (KeyError, InvalidOperation, ValueError) as exc:
            raise FatalExchangeError(
                f"OKX balance row missing/invalid field (ccy/bal/availBal required): "
                f"{exc}, details={details!r}"
            ) from exc
        return balances

    async def get_positions(self, symbol: str | None = None) -> list[Position]:  # noqa: ARG002
        """Raises the same `UnsupportedCapabilityError` shape
        `ExchangeAdapter._unsupported()` builds -- this mixin does not
        subclass `ExchangeAdapter` itself (mixin-assembly pattern, see
        module docstring), so `self._unsupported` is not available before
        `factory.py` combines it with the ABC; constructing the error
        directly keeps the exact same (capability, adapter) shape."""
        raise UnsupportedCapabilityError("get_positions", type(self).__name__)

    async def get_open_orders(
        self: _OKXAccountClient, symbol: str | None = None
    ) -> list[Order]:
        params = {"instId": symbol} if symbol is not None else None
        raw = await self._request("GET", _OPEN_ORDERS_PATH, params=params)
        data = raw.get("data")
        if data is None:
            raise FatalExchangeError(f"OKX open-orders response missing data array: {raw!r}")
        return [row_to_order(row) for row in data]

    async def get_fills(
        self: _OKXAccountClient,
        symbol: str | None = None,
        *,
        order_id: str | None = None,
        since: datetime | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {}
        if symbol is not None:
            params["instId"] = symbol
        if order_id is not None:
            params["ordId"] = order_id
        if since is not None:
            params["begin"] = _to_epoch_ms(since)
        raw = await self._request("GET", _FILLS_PATH, params=params or None)
        data = raw.get("data")
        if data is None:
            raise FatalExchangeError(f"OKX fills response missing data array: {raw!r}")
        return list(data)

    async def get_order(self: _OKXAccountClient, order_id: str) -> Order:
        inst_id, ord_id = _split_exchange_order_id(order_id)
        raw = await self._request(
            "GET", _ORDER_PATH, params={"instId": inst_id, "ordId": ord_id}
        )
        data = raw.get("data")
        if not data:
            raise FatalExchangeError(f"OKX order not found: order_id={order_id}")
        return row_to_order(data[0])
