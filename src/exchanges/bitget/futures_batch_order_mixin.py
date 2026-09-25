"""02b_bitget_api_v2_full_spec_v1.md sec5.4 -- BitgetAdapter Futures batch
order place/cancel methods.

Spec: 02b_bitget_api_v2_full_spec_v1.md sec5.4(P2)

2026-09-25 task-6797(P6.line_cap) -- split out of `futures_trading_mixin.py`
because adding these would exceed the 300-line cap (pure move, no
behavior change). Same principle as
trading_mixin.py::place_batch_orders/cancel_batch_orders (spot); these
move funds so `@require_paper_sandbox` is mandatory.

Endpoints (best-effort from community SDK reference, unverified live):
- POST /api/v2/mix/order/batch-place-order
- POST /api/v2/mix/order/batch-cancel-orders
"""

from __future__ import annotations

from typing import Any

from src.data.models.trading import Order, OrderStatus
from src.exchanges.bitget.futures_market_mixin import DEFAULT_PRODUCT_TYPE
from src.exchanges.bitget.symbols import to_bitget_symbol as _to_bitget_symbol
from src.exchanges.common.http_client import SignedRequestClient
from src.exchanges.common.live_guard import require_paper_sandbox


class BitgetFuturesBatchOrderMixin:
    @require_paper_sandbox
    async def place_futures_batch_orders(
        self: SignedRequestClient,
        orders: list[Order],
        *,
        margin_coin: str = "USDT",
        margin_mode: str = "crossed",
        product_type: str = DEFAULT_PRODUCT_TYPE,
    ) -> list[Order]:
        """Futures version of trading_mixin.py::place_batch_orders (spot).
        Bitget V2 batch-place-order only allows a batch within a single
        symbol (best-effort from community SDK reference, unverified live)."""
        if not orders:
            return []
        symbol = orders[0].symbol
        order_list: list[dict[str, Any]] = []
        for order in orders:
            row: dict[str, Any] = {
                "side": order.side.value.lower(),
                "orderType": order.order_type.value.lower(),
                "size": str(order.quantity),
                "clientOid": order.client_order_id,
            }
            if order.price is not None:
                row["price"] = str(order.price.amount)
            order_list.append(row)

        raw = await self._request(
            "POST",
            "/api/v2/mix/order/batch-place-order",
            body={
                "symbol": _to_bitget_symbol(symbol),
                "productType": product_type,
                "marginMode": margin_mode,
                "marginCoin": margin_coin,
                "orderList": order_list,
            },
        )
        data = raw["data"]
        success_by_client_oid = {item["clientOid"]: item for item in data.get("successList", [])}
        failed_client_oids = {item["clientOid"] for item in data.get("failureList", [])}

        result = []
        for order in orders:
            if order.client_order_id in success_by_client_oid:
                success = success_by_client_oid[order.client_order_id]
                result.append(
                    order.model_copy(
                        update={
                            "exchange_order_id": success["orderId"],
                            "status": OrderStatus.SUBMITTED,
                        }
                    )
                )
            elif order.client_order_id in failed_client_oids:
                result.append(order.model_copy(update={"status": OrderStatus.REJECTED}))
            else:
                result.append(order)
        return result

    @require_paper_sandbox
    async def cancel_futures_batch_orders(
        self: SignedRequestClient,
        order_ids: list[str] | None = None,
        *,
        symbol: str,
        product_type: str = DEFAULT_PRODUCT_TYPE,
    ) -> bool:
        """Futures version of trading_mixin.py::cancel_batch_orders (spot).
        Omitting `order_ids` cancels all open orders for the symbol
        (best-effort from community SDK reference, unverified live)."""
        body: dict[str, Any] = {
            "symbol": _to_bitget_symbol(symbol),
            "productType": product_type,
        }
        if order_ids is not None:
            body["orderIdList"] = [{"orderId": oid} for oid in order_ids]
        raw = await self._request("POST", "/api/v2/mix/order/batch-cancel-orders", body=body)
        return bool(raw.get("code") == "00000")
