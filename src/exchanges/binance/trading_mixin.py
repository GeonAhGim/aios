"""BR-22d -- BinanceTradingMixin: place/cancel/modify order.

Spec: docs/exchanges/ADDING_AN_EXCHANGE.md step 6(d).

Endpoints (verified 2026-09-26 against the official Binance Spot API
documentation, github.com/binance/binance-spot-api-docs, `rest-api.md`
§"Trading endpoints"):
- POST /api/v3/order -- place order. Required: symbol, side (BUY/SELL),
  type (MARKET/LIMIT), quantity, newClientOrderId. LIMIT additionally
  requires timeInForce (GTC here) + price. Response: orderId (int),
  status ("NEW" on acceptance).
- DELETE /api/v3/order -- cancel order. Required: symbol, orderId.
  Response: status ("CANCELED" on success).
- PUT /api/v3/order/cancelReplace -- Binance spot has no in-place
  amend; the documented modify pattern is cancel-then-replace with
  cancelReplaceMode="STOP_ON_FAILURE" (same "modify == cancel-replace"
  convention as Bitget's cancel-replace-order, trading_mixin.py).
  Required: symbol, side, type, cancelReplaceMode, cancelOrderId,
  quantity, newClientOrderId; LIMIT additionally requires timeInForce +
  price.

`newClientOrderId` is Binance's idempotency key (same contract as
Bitget's clientOid, common/adapter.py's `supports_client_order_id`) --
retransmitting a request with the same key does not create a duplicate
order. `place_order` always sends `order.client_order_id`; `modify_order`
requires a `client_order_id` kwarg for the same reason (the cancelReplace
call places a brand-new order, so it needs its own idempotency key) and
fails closed if it is missing or empty. quantity/price are rejected
before the exchange call if they are <= 0, NaN, or infinite (same
pre-validation convention as `okx/trading_mixin.py`'s `_validate_order`).

Deviation: `ExchangeAdapter.cancel_order(order_id)`/`modify_order(order_id)`
only accept a single string, but Binance's cancel/cancelReplace endpoints
require `symbol` alongside the numeric `orderId` -- for the same reason as
KIS's "orgno:odno"/Kiwoom's "stk_cd:ord_no" composite convention,
`place_order()` synthesizes `exchange_order_id` as "{symbol}:{orderId}" and
`cancel_order`/`modify_order` expect that same format (task-7997:
`account_mixin.py::BinanceAccountMixin.get_order()` follows the same
"{symbol}:{orderId}" format -- a future `BinanceAdapter` combining both
mixins satisfies `_OrderMutatingClient` below with no further wiring).

Every method in this file moves funds, so every one carries
`@require_paper_sandbox` with no exceptions (same convention as
bitget/kis/kiwoom trading_mixin.py; the AST scanner in
`tests/unit/exchanges/test_live_guard_coverage.py` enforces this
repo-wide). This is independent of, and in addition to, the
`AIOS_ALLOW_LIVE_ADAPTER` fail-closed construction-time guard in
`src/exchanges/factory.py` (§6 of the adding-an-exchange doc) -- that
guard will apply to Binance with no exception once factory registration
wires this adapter in; nothing in this mixin bypasses it.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Protocol

from src.core.exceptions import FatalExchangeError
from src.data.models.trading import Order, OrderSide, OrderStatus, OrderType
from src.exchanges.common.live_guard import require_paper_sandbox

_ORDER_PATH = "/api/v3/order"
_CANCEL_REPLACE_PATH = "/api/v3/order/cancelReplace"
_TIME_IN_FORCE_GTC = "GTC"
_CANCEL_REPLACE_MODE = "STOP_ON_FAILURE"


def _split_exchange_order_id(exchange_order_id: str) -> tuple[str, str]:
    if ":" not in exchange_order_id:
        raise FatalExchangeError(
            f"Binance exchange_order_id must be 'symbol:orderId' format: {exchange_order_id}"
        )
    symbol, order_id = exchange_order_id.split(":", 1)
    return symbol, order_id


def _validate_quantity(quantity: Decimal) -> None:
    if quantity.is_nan() or quantity.is_infinite() or quantity <= 0:
        raise FatalExchangeError(f"Binance order quantity must be finite and > 0: {quantity!r}")


def _validate_price(price: Decimal) -> None:
    if price.is_nan() or price.is_infinite() or price <= 0:
        raise FatalExchangeError(f"Binance LIMIT order price must be finite and > 0: {price!r}")


def _validate_client_order_id(client_order_id: str) -> None:
    """Binance's newClientOrderId is the idempotency key that prevents a
    retried request from creating a duplicate order (same contract as
    Bitget's clientOid) -- a missing/empty key fails closed instead of
    letting the request go out unkeyed."""
    if not client_order_id:
        raise FatalExchangeError(
            "Binance order requires a non-empty client_order_id (newClientOrderId)"
        )


class _BinanceOrderClient(Protocol):
    """Minimal HTTP contract needed at mixin-assembly time (same reasoning
    as kiwoom/trading_mixin.py's _KiwoomOrderClient -- declared locally
    here since the concrete signed-request client for Binance is not yet
    registered in `src/exchanges/common/http_client.py`)."""

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...


class _OrderMutatingClient(_BinanceOrderClient, Protocol):
    """modify_order() calls get_order() on the same assembled instance --
    included explicitly in the contract for the same reason as Kiwoom
    trading_mixin._OrderMutatingClient (get_order lives in
    account_mixin.py, implemented by BinanceAccountMixin as of task-7997;
    this file's own tests still supply a stub since no BinanceAdapter
    combining both mixins exists yet)."""

    async def get_order(self, order_id: str) -> Order: ...


class BinanceTradingMixin:
    @require_paper_sandbox
    async def place_order(self: _BinanceOrderClient, order: Order) -> Order:
        _validate_client_order_id(order.client_order_id)
        _validate_quantity(order.quantity)
        params: dict[str, Any] = {
            "symbol": order.symbol,
            "side": order.side.value,
            "type": order.order_type.value,
            "quantity": str(order.quantity),
            "newClientOrderId": order.client_order_id,
        }
        if order.order_type == OrderType.LIMIT:
            if order.price is None:
                raise FatalExchangeError("Binance LIMIT order requires a price")
            _validate_price(order.price.amount)
            params["timeInForce"] = _TIME_IN_FORCE_GTC
            params["price"] = str(order.price.amount)
        raw = await self._request("POST", _ORDER_PATH, params=params)
        try:
            order_id = raw["orderId"]
        except KeyError as exc:
            raise FatalExchangeError(
                f"Binance order response missing expected field: {exc}"
            ) from exc
        exchange_order_id = f"{order.symbol}:{order_id}"
        return order.model_copy(
            update={"exchange_order_id": exchange_order_id, "status": OrderStatus.SUBMITTED}
        )

    @require_paper_sandbox
    async def cancel_order(self: _BinanceOrderClient, order_id: str) -> bool:
        symbol, binance_order_id = _split_exchange_order_id(order_id)
        params: dict[str, Any] = {"symbol": symbol, "orderId": binance_order_id}
        raw = await self._request("DELETE", _ORDER_PATH, params=params)
        return raw.get("status") == "CANCELED"

    @require_paper_sandbox
    async def modify_order(self: _OrderMutatingClient, order_id: str, **kwargs: Any) -> Order:
        """Binance spot has no in-place amend endpoint -- `cancelReplace`
        cancels the existing order and atomically places a new one, so a
        modify here requires the full replacement order shape (symbol,
        side, type, quantity, client_order_id), not just the changed field
        (same fail-closed pre-validation style as
        KiwoomTradingMixin.modify_order rejecting a price-less modify).
        `client_order_id` is sent as cancelReplace's `newClientOrderId` --
        the idempotency key for the replacement order, same contract as
        place_order's `newClientOrderId`."""
        for required in ("side", "order_type", "quantity", "client_order_id"):
            if required not in kwargs:
                raise FatalExchangeError(
                    f"Binance modify_order (cancelReplace) requires '{required}' -- "
                    "called without it"
                )
        symbol, binance_order_id = _split_exchange_order_id(order_id)
        side: OrderSide = kwargs["side"]
        order_type: OrderType = kwargs["order_type"]
        quantity: Decimal = kwargs["quantity"]
        _validate_client_order_id(kwargs["client_order_id"])
        _validate_quantity(quantity)
        params: dict[str, Any] = {
            "symbol": symbol,
            "side": side.value,
            "type": order_type.value,
            "cancelReplaceMode": _CANCEL_REPLACE_MODE,
            "cancelOrderId": binance_order_id,
            "quantity": str(quantity),
            "newClientOrderId": kwargs["client_order_id"],
        }
        if order_type == OrderType.LIMIT:
            if "price" not in kwargs:
                raise FatalExchangeError("Binance LIMIT modify_order requires a price")
            price: Decimal = kwargs["price"]
            _validate_price(price)
            params["timeInForce"] = _TIME_IN_FORCE_GTC
            params["price"] = str(price)
        raw = await self._request("PUT", _CANCEL_REPLACE_PATH, params=params)
        try:
            new_order_id = raw["newOrderResponse"]["orderId"]
        except KeyError as exc:
            raise FatalExchangeError(
                f"Binance cancelReplace response missing expected field: {exc}"
            ) from exc
        return await self.get_order(f"{symbol}:{new_order_id}")
