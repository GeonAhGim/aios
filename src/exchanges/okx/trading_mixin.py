"""BR-21d -- OKXTradingMixin: place/cancel/modify order.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md#BR-21,
docs/exchanges/ADDING_AN_EXCHANGE.md step 6(d).

Endpoints (verified 2026-09-26 against the official OKX v5 API reference,
www.okx.com/docs-v5/en, Order Book Trading > Trade section):
- POST /api/v5/trade/order -- instId/tdMode/side/ordType/sz required,
  px required for `limit`. Response wraps a `data` array of
  {ordId, clOrdId, sCode, sMsg, tag}; a non-"0" top-level `code` means the
  whole batch failed, a non-"0" `sCode` on an individual `data` row means
  that specific order failed (OKX distinguishes batch-level vs order-level
  failure this way -- unlike Bitget/KIS/NH single top-level code).
- POST /api/v5/trade/cancel-order -- instId + (ordId or clOrdId).
- POST /api/v5/trade/amend-order -- instId + (ordId or clOrdId) +
  (newSz and/or newPx). OKX rejects an amend request that supplies
  neither newSz nor newPx (nothing to change).

Deviation: OKX's cancel/amend requests require `instId` alongside `ordId`,
but `ExchangeAdapter.cancel_order(order_id)`/`modify_order(order_id)` only
accept a single string -- same reason as KIS's "orgno:odno" and Kiwoom's
"stk_cd:ord_no" composite convention, `place_order()` synthesizes
`exchange_order_id` as "{instId}:{ordId}" and `cancel_order`/
`modify_order` expect that same format.

Unverified scope (ratchet note): `order.order_type` currently only has
MARKET/LIMIT (src/data/models/trading.py), so `_to_okx_ord_type` only maps
those two -- OKX's `post_only`/`fok`/`ioc` order types are out of this
leaf's domain-model scope and are not guessed at; any other order_type
value reaching this mixin is rejected fail-closed (see `_to_okx_ord_type`).
Spot-only, cash trade mode (`tdMode="cash"`, Phase 1 scope per 06 doc
§6.1) -- margin/futures trade modes are a separate leaf.

Every method in this file moves funds, so every one carries
`@require_paper_sandbox` with no exceptions (same convention as
bitget/kis/nh/kiwoom trading_mixin.py; the AST scanner in
`tests/unit/exchanges/test_live_guard_coverage.py` enforces this
repo-wide). This is independent of, and in addition to, the
`AIOS_ALLOW_LIVE_ADAPTER` fail-closed construction-time guard in
`src/exchanges/factory.py` (§6 of the adding-an-exchange doc) -- that
guard applies to OKX with no exception once factory registration
(task BR-21b) wires this adapter in; nothing in this mixin bypasses it.
"""

from __future__ import annotations

from typing import Any, Protocol

from src.core.exceptions import FatalExchangeError
from src.data.models.trading import Order, OrderSide, OrderStatus, OrderType
from src.exchanges.common.live_guard import require_paper_sandbox

_TRADE_MODE_CASH = "cash"  # spot-only Phase 1 scope (module docstring)
_ORDER_PATH = "/api/v5/trade/order"
_CANCEL_PATH = "/api/v5/trade/cancel-order"
_AMEND_PATH = "/api/v5/trade/amend-order"


def _to_okx_side(side: OrderSide) -> str:
    return "buy" if side == OrderSide.BUY else "sell"


def _to_okx_ord_type(order_type: OrderType) -> str:
    """Fail-closed -- only MARKET/LIMIT are representable by our domain
    Order model today (see module docstring). Any other value reaching
    here (a future domain OrderType member not yet mapped) is rejected
    instead of guessing an OKX enum value."""
    if order_type == OrderType.LIMIT:
        return "limit"
    if order_type == OrderType.MARKET:
        return "market"
    raise FatalExchangeError(f"OKX가 지원하지 않는 주문 타입: {order_type!r}")


def _validate_order(order: Order) -> None:
    if order.quantity <= 0:
        raise FatalExchangeError(f"OKX 주문 수량은 0보다 커야 함: {order.quantity!r}")
    if order.order_type == OrderType.LIMIT and (order.price is None or order.price.amount <= 0):
        raise FatalExchangeError(
            f"OKX 지정가(limit) 주문은 0보다 큰 가격이 필요함: {order.price!r}"
        )


def _split_exchange_order_id(exchange_order_id: str) -> tuple[str, str]:
    if ":" not in exchange_order_id:
        raise FatalExchangeError(
            f"OKX exchange_order_id는 'instId:ordId' 형식이어야 함: {exchange_order_id}"
        )
    inst_id, ord_id = exchange_order_id.split(":", 1)
    return inst_id, ord_id


def _first_data_row(raw: dict[str, Any], *, path: str) -> dict[str, Any]:
    """OKX reuses its batch-order response shape even for a single order --
    if `data` is an empty array (the batch itself passed with code=="0" but
    carries no row, a malformed response), fail immediately instead of
    silently raising an index error."""
    data = raw.get("data")
    if not data:
        raise FatalExchangeError(f"OKX {path} 응답에 data 배열이 비어 있음: {raw!r}")
    row: dict[str, Any] = data[0]
    s_code = row.get("sCode")
    if s_code is not None and s_code != "0":
        raise FatalExchangeError(
            f"OKX {path} 주문 실패(sCode={s_code!r}, sMsg={row.get('sMsg')!r}): {row!r}"
        )
    return row


class _OKXOrderClient(Protocol):
    """Minimal HTTP contract needed at mixin-assembly time (same reasoning
    as common/http_client.py's KISHTTPClient/NHHTTPClient, and
    kiwoom/trading_mixin.py's local `_KiwoomOrderClient` -- declared
    locally here).

    auth.py (task BR-21b) is not yet implemented, so a shared
    `OKXHTTPClient` cannot yet be registered in
    `src/exchanges/common/http_client.py`. Once the concrete class
    satisfies this structural type (method name/signature), it wires in
    automatically at assembly time with no runtime behavior change."""

    async def _request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...


class _OrderMutatingClient(_OKXOrderClient, Protocol):
    """modify_order() calls get_order() on the same assembled instance --
    included explicitly in the contract for the same reason as
    KIS/Kiwoom trading_mixin._OrderMutatingClient (get_order lives in
    account_mixin.py, task BR-21c)."""

    async def get_order(self, order_id: str) -> Order: ...


class OKXTradingMixin:
    @require_paper_sandbox
    async def place_order(self: _OKXOrderClient, order: Order) -> Order:
        _validate_order(order)
        body: dict[str, Any] = {
            "instId": order.symbol,
            "tdMode": _TRADE_MODE_CASH,
            "side": _to_okx_side(order.side),
            "ordType": _to_okx_ord_type(order.order_type),
            "sz": str(order.quantity),
            "clOrdId": order.client_order_id,
        }
        if order.price is not None:
            body["px"] = str(order.price.amount)
        raw = await self._request("POST", _ORDER_PATH, body=body)
        row = _first_data_row(raw, path=_ORDER_PATH)
        try:
            ord_id = row["ordId"]
        except KeyError as exc:
            raise FatalExchangeError(f"OKX 주문 응답에 ordId 필드 없음: {row!r}") from exc
        exchange_order_id = f"{order.symbol}:{ord_id}"
        return order.model_copy(
            update={"exchange_order_id": exchange_order_id, "status": OrderStatus.SUBMITTED}
        )

    @require_paper_sandbox
    async def cancel_order(self: _OKXOrderClient, order_id: str) -> bool:
        inst_id, ord_id = _split_exchange_order_id(order_id)
        body: dict[str, Any] = {"instId": inst_id, "ordId": ord_id}
        raw = await self._request("POST", _CANCEL_PATH, body=body)
        _first_data_row(raw, path=_CANCEL_PATH)
        return True

    @require_paper_sandbox
    async def modify_order(self: _OrderMutatingClient, order_id: str, **kwargs: Any) -> Order:
        """OKX's amend-order endpoint rejects a request that supplies
        neither `newSz` nor `newPx` (nothing to change) -- reject before
        reaching the exchange (same fail-closed pre-validation style as
        BitgetTradingMixin.modify_order/KiwoomTradingMixin.modify_order).

        kwargs key names follow the repo-wide `price`/`size` convention
        used by the actual caller (src/services/order_service/modify.py)
        and by Bitget/Kiwoom; KIS's `quantity` is not accepted here since
        OKX has no such precedent to preserve."""
        if "price" not in kwargs and "size" not in kwargs:
            raise FatalExchangeError(
                "OKX amend-order는 newSz/newPx 중 최소 하나가 필요함"
                "(둘 다 없는 정정 요청은 거래소가 거부함)"
            )
        inst_id, ord_id = _split_exchange_order_id(order_id)
        body: dict[str, Any] = {"instId": inst_id, "ordId": ord_id}
        if "price" in kwargs:
            body["newPx"] = str(kwargs["price"])
        if "size" in kwargs:
            body["newSz"] = str(kwargs["size"])
        raw = await self._request("POST", _AMEND_PATH, body=body)
        _first_data_row(raw, path=_AMEND_PATH)
        return await self.get_order(order_id)
