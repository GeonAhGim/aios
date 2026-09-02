"""02b_bitget_api_v2_full_spec_v1.md §5.4 — BitgetAdapter Futures Order(Trade) 메서드군.

Spec: 02b_bitget_api_v2_full_spec_v1.md §5.4, P0

엔드포인트(커뮤니티 SDK 레퍼런스 기준, 라이브 검증 필요):
- POST /api/v2/mix/order/place-order
- POST /api/v2/mix/order/modify-order
- POST /api/v2/mix/order/cancel-order
- POST /api/v2/mix/order/close-positions   (FD-9.2 Watchdog LIQUIDATE 집행 경로 후보)
- GET  /api/v2/mix/order/detail
- GET  /api/v2/mix/order/fills
- GET  /api/v2/mix/order/orders-history
- GET  /api/v2/mix/order/orders-pending
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

from src.data.models.base import AssetClass
from src.data.models.trading import Order, OrderSide, OrderStatus, OrderType
from src.exchanges.bitget.futures_market_mixin import DEFAULT_PRODUCT_TYPE, _to_bitget_symbol

_STATUS_MAP = {
    "live": OrderStatus.ACKNOWLEDGED,
    "partially_filled": OrderStatus.PARTIALLY_FILLED,
    "filled": OrderStatus.FILLED,
    "canceled": OrderStatus.CANCELLED,
}


def _map_status(raw_status: str) -> OrderStatus:
    return _STATUS_MAP.get(raw_status, OrderStatus.UNKNOWN)


def _row_to_futures_order(data: dict[str, Any]) -> Order:
    status = _map_status(data.get("state", data.get("status", "")))
    return Order(
        order_id=uuid4(),
        exchange_order_id=data["orderId"],
        client_order_id=data.get("clientOid", ""),
        strategy_id="",
        strategy_version="",
        symbol=data.get("symbol", ""),
        exchange="bitget",
        side=OrderSide(data["side"].upper()) if "side" in data else OrderSide.BUY,
        order_type=(
            OrderType(data["orderType"].upper()) if "orderType" in data else OrderType.MARKET
        ),
        quantity=Decimal(data.get("size", "0")),
        status=status,
        filled_quantity=Decimal(data.get("baseVolume", "0")),
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        asset_class=AssetClass.CRYPTO,
    )


class BitgetFuturesTradingMixin:
    async def place_futures_order(
        self,
        order: Order,
        *,
        margin_coin: str = "USDT",
        margin_mode: str = "crossed",
        product_type: str = DEFAULT_PRODUCT_TYPE,
    ) -> Order:
        body: dict[str, Any] = {
            "symbol": _to_bitget_symbol(order.symbol),
            "productType": product_type,
            "marginMode": margin_mode,
            "marginCoin": margin_coin,
            "side": order.side.value.lower(),
            "orderType": order.order_type.value.lower(),
            "size": str(order.quantity),
            "clientOid": order.client_order_id,
        }
        if order.price is not None:
            body["price"] = str(order.price.amount)

        raw = await self._request(  # type: ignore[attr-defined]
            "POST", "/api/v2/mix/order/place-order", body=body
        )
        data = raw["data"]
        return order.model_copy(
            update={"exchange_order_id": data["orderId"], "status": OrderStatus.SUBMITTED}
        )

    async def modify_futures_order(
        self,
        order_id: str,
        *,
        symbol: str,
        product_type: str = DEFAULT_PRODUCT_TYPE,
        **kwargs: Any,
    ) -> Order:
        """Spot과 달리 Futures는 취소 후 재주문이 아니라 실제 정정
        엔드포인트를 지원한다(02b §5.4 비고)."""
        body: dict[str, Any] = {
            "orderId": order_id,
            "symbol": _to_bitget_symbol(symbol),
            "productType": product_type,
        }
        if "price" in kwargs:
            body["newPrice"] = str(kwargs["price"])
        if "size" in kwargs:
            body["newSize"] = str(kwargs["size"])

        raw = await self._request(  # type: ignore[attr-defined]
            "POST", "/api/v2/mix/order/modify-order", body=body
        )
        data = raw["data"]
        return await self.get_futures_order(data["orderId"], symbol=symbol)

    async def cancel_futures_order(
        self, order_id: str, *, symbol: str, product_type: str = DEFAULT_PRODUCT_TYPE
    ) -> bool:
        raw = await self._request(  # type: ignore[attr-defined]
            "POST",
            "/api/v2/mix/order/cancel-order",
            body={
                "orderId": order_id,
                "symbol": _to_bitget_symbol(symbol),
                "productType": product_type,
            },
        )
        return bool(raw.get("code") == "00000")

    async def close_futures_position(
        self,
        symbol: str,
        *,
        margin_coin: str = "USDT",
        product_type: str = DEFAULT_PRODUCT_TYPE,
    ) -> bool:
        """FD-9.2(Watchdog LIQUIDATE 판정) 실제 집행 경로 후보(02b §5.4) —
        지금은 이 메서드를 호출하는 안전장치 배선이 없다(Phase 1 크립토
        현물 전용, Futures 강제청산 연동은 파생상품 확장 시 별도 leaf)."""
        raw = await self._request(  # type: ignore[attr-defined]
            "POST",
            "/api/v2/mix/order/close-positions",
            body={
                "symbol": _to_bitget_symbol(symbol),
                "productType": product_type,
                "marginCoin": margin_coin,
            },
        )
        return bool(raw.get("code") == "00000")

    async def get_futures_order(
        self, order_id: str, *, symbol: str, product_type: str = DEFAULT_PRODUCT_TYPE
    ) -> Order:
        raw = await self._request(  # type: ignore[attr-defined]
            "GET",
            "/api/v2/mix/order/detail",
            params={
                "orderId": order_id,
                "symbol": _to_bitget_symbol(symbol),
                "productType": product_type,
            },
        )
        data = raw["data"][0] if isinstance(raw["data"], list) else raw["data"]
        return _row_to_futures_order(data)

    async def get_futures_open_orders(
        self, *, symbol: str | None = None, product_type: str = DEFAULT_PRODUCT_TYPE
    ) -> list[Order]:
        params: dict[str, Any] = {"productType": product_type}
        if symbol is not None:
            params["symbol"] = _to_bitget_symbol(symbol)
        raw = await self._request(  # type: ignore[attr-defined]
            "GET", "/api/v2/mix/order/orders-pending", params=params
        )
        return [_row_to_futures_order(row) for row in raw["data"].get("entrustedList") or []]

    async def get_futures_order_history(
        self, *, symbol: str | None = None, product_type: str = DEFAULT_PRODUCT_TYPE
    ) -> list[Order]:
        params: dict[str, Any] = {"productType": product_type}
        if symbol is not None:
            params["symbol"] = _to_bitget_symbol(symbol)
        raw = await self._request(  # type: ignore[attr-defined]
            "GET", "/api/v2/mix/order/orders-history", params=params
        )
        return [_row_to_futures_order(row) for row in raw["data"].get("entrustedList") or []]

    async def get_futures_fills(
        self, *, symbol: str | None = None, product_type: str = DEFAULT_PRODUCT_TYPE
    ) -> list[dict[str, Any]]:
        """02b §2 모델 재사용 원칙 — 이 데이터를 소비하는 호출부가 생기기
        전까지 raw dict를 그대로 반환한다(trading_mixin.py::get_fills와
        동일 판단)."""
        params: dict[str, Any] = {"productType": product_type}
        if symbol is not None:
            params["symbol"] = _to_bitget_symbol(symbol)
        raw = await self._request(  # type: ignore[attr-defined]
            "GET", "/api/v2/mix/order/fills", params=params
        )
        return list(raw["data"].get("fillList") or [])
