"""BR-23d -- KiwoomTradingMixin: place/cancel/modify order.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md#BR-23,
docs/exchanges/ADDING_AN_EXCHANGE.md step 6(d).

Endpoints (verified 2026-09-26 against the official Kiwoom Securities REST
API client repository, github.com/Kiwoom-Securities/Kiwoom-REST-API,
`kiwoom/core/client.py` + `examples/domestic_stock/order/*.py`):
- POST /api/dostk/ordr, TR distinguished by the `api-id` header --
  kt10000 (buy), kt10001 (sell), kt10002 (modify), kt10003 (cancel).
  Live base_url is https://api.kiwoom.com, sandbox is
  https://mockapi.kiwoom.com (adapter assembly/auth is task-7569's auth.py).
- Buy/sell request body: dmst_stex_tp (domestic exchange division) /
  stk_cd (symbol) / ord_qty (quantity) / trde_tp (trade type, "0"=limit,
  "3"=market) / ord_uv (order price) / cond_uv (conditional price).
  Response: return_code/return_msg + ord_no (7-digit order number).
- Cancel body: dmst_stex_tp/orig_ord_no (original order number)/stk_cd/
  cncl_qty ("0" = cancel full remaining quantity). Modify body:
  dmst_stex_tp/orig_ord_no/stk_cd/mdfy_qty ("0" = modify full remaining
  quantity)/mdfy_uv (modify price, required)/mdfy_cond_uv.

Deviation: Kiwoom's cancel/modify requests require the symbol code
(stk_cd) alongside the original order number (orig_ord_no) on every call,
but `ExchangeAdapter.cancel_order(order_id)`/`modify_order(order_id)` only
accept a single string -- for the same reason as KIS's "orgno:odno"
composite convention, `place_order()` synthesizes `exchange_order_id` as
"{symbol}:{ord_no}" and `cancel_order`/`modify_order` expect that same
format (a documented convenience convention -- account_mixin's
`get_order()`, task-7570, must follow the same format).

Unverified scope (ratchet note): `order.order_type` currently only has
MARKET/LIMIT, so trde_tp only handles "0"/"3" -- other trde_tp values
(conditional-price, after-hours, ...) are out of this leaf's scope and are
not guessed.

Every method in this file moves funds, so every one carries
`@require_paper_sandbox` with no exceptions (same convention as
bitget/kis trading_mixin.py; the AST scanner in
`tests/unit/exchanges/test_live_guard_coverage.py` enforces this
repo-wide).
"""

from __future__ import annotations

from typing import Any, Protocol

from src.core.exceptions import FatalExchangeError
from src.data.models.trading import Order, OrderSide, OrderStatus, OrderType
from src.exchanges.common.live_guard import require_paper_sandbox

_DOMESTIC_EXCHANGE_DIVISION = "KRX"  # Phase 1 target (06 doc §6.1), same convention as KIS
_ORDER_PATH = "/api/dostk/ordr"
_API_ID_BUY = "kt10000"
_API_ID_SELL = "kt10001"
_API_ID_MODIFY = "kt10002"
_API_ID_CANCEL = "kt10003"


def _trade_division(order_type: OrderType) -> str:
    return "3" if order_type == OrderType.MARKET else "0"


def _split_exchange_order_id(exchange_order_id: str) -> tuple[str, str]:
    if ":" not in exchange_order_id:
        raise FatalExchangeError(
            f"Kiwoom exchange_order_id must be 'stk_cd:ord_no' format: {exchange_order_id}"
        )
    stk_cd, ord_no = exchange_order_id.split(":", 1)
    return stk_cd, ord_no


class _KiwoomOrderClient(Protocol):
    """Minimal HTTP contract needed at mixin-assembly time (same reasoning
    as common/http_client.py's KISHTTPClient/NHHTTPClient -- declared
    locally here).

    auth.py (task-7569) is in flight in parallel with this leaf, so
    `KiwoomHTTPClient` cannot yet be registered in the shared
    `src/exchanges/common/http_client.py`. Once the concrete class
    satisfies this structural type (method name/signature), it wires in
    automatically at assembly time with no runtime behavior change."""

    async def _request(
        self,
        method: str,
        path: str,
        api_id: str,
        *,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...


class _OrderMutatingClient(_KiwoomOrderClient, Protocol):
    """modify_order() calls get_order() on the same assembled instance --
    included explicitly in the contract for the same reason as KIS
    trading_mixin._OrderMutatingClient (get_order lives in account_mixin,
    task-7570)."""

    async def get_order(self, order_id: str) -> Order: ...


class KiwoomTradingMixin:
    @require_paper_sandbox
    async def place_order(self: _KiwoomOrderClient, order: Order) -> Order:
        body: dict[str, Any] = {
            "dmst_stex_tp": _DOMESTIC_EXCHANGE_DIVISION,
            "stk_cd": order.symbol,
            "ord_qty": str(order.quantity),
            "trde_tp": _trade_division(order.order_type),
            "ord_uv": str(order.price.amount) if order.price is not None else "",
            "cond_uv": "",
        }
        api_id = _API_ID_BUY if order.side == OrderSide.BUY else _API_ID_SELL
        raw = await self._request("POST", _ORDER_PATH, api_id, body=body)
        try:
            ord_no = raw["ord_no"]
        except KeyError as exc:
            raise FatalExchangeError(
                f"Kiwoom order response missing expected field: {exc}"
            ) from exc
        exchange_order_id = f"{order.symbol}:{ord_no}"
        return order.model_copy(
            update={"exchange_order_id": exchange_order_id, "status": OrderStatus.SUBMITTED}
        )

    @require_paper_sandbox
    async def cancel_order(self: _KiwoomOrderClient, order_id: str) -> bool:
        stk_cd, orig_ord_no = _split_exchange_order_id(order_id)
        body: dict[str, Any] = {
            "dmst_stex_tp": _DOMESTIC_EXCHANGE_DIVISION,
            "orig_ord_no": orig_ord_no,
            "stk_cd": stk_cd,
            "cncl_qty": "0",  # cancel full remaining quantity (documented sample convention)
        }
        raw = await self._request("POST", _ORDER_PATH, _API_ID_CANCEL, body=body)
        return raw.get("return_code") in (None, 0)

    @require_paper_sandbox
    async def modify_order(self: _OrderMutatingClient, order_id: str, **kwargs: Any) -> Order:
        """Kiwoom's modify-order TR (kt10002) requires a modify price
        (mdfy_uv) -- a quantity-only modification is not supported by this
        API, so calling without a `price` kwarg is rejected before
        reaching the exchange (same fail-closed pre-validation style as
        BitgetTradingMixin.modify_order rejecting market-order modifies)."""
        if "price" not in kwargs:
            raise FatalExchangeError(
                "Kiwoom modify-order (kt10002) requires a price -- called without one"
            )
        stk_cd, orig_ord_no = _split_exchange_order_id(order_id)
        quantity = kwargs.get("quantity")
        body: dict[str, Any] = {
            "dmst_stex_tp": _DOMESTIC_EXCHANGE_DIVISION,
            "orig_ord_no": orig_ord_no,
            "stk_cd": stk_cd,
            "mdfy_qty": str(quantity) if quantity is not None else "0",
            "mdfy_uv": str(kwargs["price"]),
            "mdfy_cond_uv": "",
        }
        await self._request("POST", _ORDER_PATH, _API_ID_MODIFY, body=body)
        return await self.get_order(order_id)
