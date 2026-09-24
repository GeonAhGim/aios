"""02b_bitget_api_v2_full_spec_v1.md Section 4 — BitgetAdapter Margin P2 methods.

Spec: 02b_bitget_api_v2_full_spec_v1.md Section 4 (P2)

Holds the 5 remaining P2 margin endpoints, split out from margin_mixin.py's
P0/P1 set (account assets/risk-rate/place/cancel/open-orders) to stay under
the 300-line file-policy cap (ADR-2026-09-10-C Section 7,
architecture_guard P6.line_cap). Validation logic is reused as-is from
margin_mixin.py's `_validate_margin_type`.

Endpoints (unverified against official docs, based on community SDK
references — actual response field names need live verification once a
Demo key is available):
- GET  /api/v2/margin/{marginType}/account/max-transfer-out-amount
- GET  /api/v2/margin/{marginType}/tier-data
- POST /api/v2/margin/{marginType}/account/flash-repay
- POST /api/v2/margin/{marginType}/batch-place-order
- GET  /api/v2/margin/{marginType}/{borrow,repay,interest,liquidation}-history
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from src.data.models.trading import Order, OrderStatus
from src.exchanges.bitget.margin_mixin import _validate_margin_type
from src.exchanges.bitget.symbols import to_bitget_symbol as _to_bitget_symbol
from src.exchanges.common.http_client import SignedRequestClient
from src.exchanges.common.live_guard import require_paper_sandbox

_MARGIN_HISTORY_TYPES = ("borrow", "repay", "interest", "liquidation")


class BitgetMarginP2Mixin:
    @require_paper_sandbox
    async def get_max_transfer_out_amount(
        self: SignedRequestClient, margin_type: str, coin: str, *, symbol: str | None = None
    ) -> Decimal:
        """02b spec Section 4 (P2) — unverified: response field names are a
        community-SDK best guess pending live verification with a Demo key
        (same caveat as the module docstring)."""
        _validate_margin_type(margin_type)
        params: dict[str, Any] = {"coin": coin.upper()}
        if symbol is not None:
            params["symbol"] = _to_bitget_symbol(symbol)
        raw = await self._request(
            "GET",
            f"/api/v2/margin/{margin_type}/account/max-transfer-out-amount",
            params=params,
        )
        data = raw["data"][0] if isinstance(raw["data"], list) else raw["data"]
        return Decimal(data["maxTransferOutAmount"])

    async def get_margin_tier_data(
        self: SignedRequestClient, margin_type: str, coin: str, *, symbol: str | None = None
    ) -> list[dict[str, Any]]:
        """02b spec Section 4 (P2) — margin tier table. Unverified; returns
        raw dicts (no consumer yet, Section 2 interface-contract principle —
        a new model waits for an actual call site)."""
        _validate_margin_type(margin_type)
        params: dict[str, Any] = {"coin": coin.upper()}
        if symbol is not None:
            params["symbol"] = _to_bitget_symbol(symbol)
        raw = await self._request("GET", f"/api/v2/margin/{margin_type}/tier-data", params=params)
        return list(raw["data"])

    @require_paper_sandbox
    async def flash_repay_margin(
        self: SignedRequestClient,
        margin_type: str,
        coin_list: list[str],
        *,
        symbol: str | None = None,
    ) -> dict[str, Any]:
        """02b spec Section 4 (P2) — same principle as red-team
        #2026-09-02-32/33 (LIVE adapter block + input sanity check).
        Unverified field names."""
        _validate_margin_type(margin_type)
        if not coin_list:
            raise ValueError("coin_list는 최소 1개 이상이어야 합니다.")
        body: dict[str, Any] = {"coinList": [coin.upper() for coin in coin_list]}
        if symbol is not None:
            body["symbol"] = _to_bitget_symbol(symbol)
        raw = await self._request(
            "POST", f"/api/v2/margin/{margin_type}/account/flash-repay", body=body
        )
        return dict(raw["data"])

    @require_paper_sandbox
    async def batch_place_margin_orders(
        self: SignedRequestClient, margin_type: str, orders: list[Order]
    ) -> list[Order]:
        """02b spec Section 4 (P2) — same principle as red-team
        #2026-09-02-32/33. Unverified field names (the `resultList` response
        shape is a guess following the spot/mix batch-endpoint convention)."""
        _validate_margin_type(margin_type)
        if not orders:
            raise ValueError("orders는 최소 1개 이상이어야 합니다.")
        order_list: list[dict[str, Any]] = []
        for order in orders:
            if order.quantity <= 0:
                raise ValueError("order.quantity는 0보다 커야 합니다.")
            item: dict[str, Any] = {
                "symbol": _to_bitget_symbol(order.symbol),
                "side": order.side.value.lower(),
                "orderType": order.order_type.value.lower(),
                "force": "gtc",
                "baseSize": str(order.quantity),
                "clientOid": order.client_order_id,
            }
            if order.price is not None:
                item["price"] = str(order.price.amount)
            order_list.append(item)

        raw = await self._request(
            "POST",
            f"/api/v2/margin/{margin_type}/batch-place-order",
            body={"orderList": order_list},
        )
        data = raw["data"]
        results = data.get("resultList", []) if isinstance(data, dict) else list(data)
        return [
            order.model_copy(
                update={
                    "exchange_order_id": result.get("orderId", ""),
                    "status": OrderStatus.SUBMITTED,
                }
            )
            for order, result in zip(orders, results, strict=True)
        ]

    async def get_margin_history(
        self: SignedRequestClient,
        margin_type: str,
        history_type: str,
        *,
        coin: str | None = None,
        symbol: str | None = None,
    ) -> list[dict[str, Any]]:
        """02b spec Section 4 (P2) — combined borrow/repay/interest/liquidation
        history lookup (for FD-20). Unverified; returns raw dicts."""
        _validate_margin_type(margin_type)
        if history_type not in _MARGIN_HISTORY_TYPES:
            raise ValueError(
                f"알 수 없는 history_type입니다: {history_type!r} "
                f"(허용값: {', '.join(_MARGIN_HISTORY_TYPES)})"
            )
        params: dict[str, Any] = {}
        if coin is not None:
            params["coin"] = coin.upper()
        if symbol is not None:
            params["symbol"] = _to_bitget_symbol(symbol)
        raw = await self._request(
            "GET",
            f"/api/v2/margin/{margin_type}/{history_type}-history",
            params=params or None,
        )
        return list(raw["data"])
