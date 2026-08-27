"""6.7 / 6.8 — BitgetAdapter Trading 메서드군 + health_check().

Spec: 02_exchange_adapter_v1.2.md#§2.1

엔드포인트(2026-08-28 문서 조사 확인 — 실제 응답은 Demo API 키로 라이브
검증 필요):
- POST /api/v2/spot/trade/place-order
- POST /api/v2/spot/trade/cancel-order
- GET  /api/v2/spot/trade/orderInfo
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

from src.data.models.base import AssetClass
from src.data.models.trading import Order, OrderSide, OrderStatus, OrderType

# 확인된 값(라이브/문서 조사) + 미확인 값은 UNKNOWN으로 안전하게 폴백
# (8.3 원칙 — 모르는 상태를 실패로 단정하지 않는다).
_STATUS_MAP = {
    "live": OrderStatus.ACKNOWLEDGED,
    "partially_filled": OrderStatus.PARTIALLY_FILLED,
    "filled": OrderStatus.FILLED,
    "cancelled": OrderStatus.CANCELLED,
}


def _map_status(raw_status: str) -> OrderStatus:
    return _STATUS_MAP.get(raw_status, OrderStatus.UNKNOWN)


class BitgetTradingMixin:
    async def place_order(self, order: Order) -> Order:
        body: dict[str, Any] = {
            "symbol": order.symbol.replace("/", ""),
            "side": order.side.value.lower(),
            "orderType": order.order_type.value.lower(),
            "force": "gtc",
            "size": str(order.quantity),
            "clientOid": order.client_order_id,
        }
        if order.price is not None:
            body["price"] = str(order.price.amount)

        raw = await self._request(  # type: ignore[attr-defined]
            "POST", "/api/v2/spot/trade/place-order", body=body
        )
        data = raw["data"]
        return order.model_copy(
            update={"exchange_order_id": data["orderId"], "status": OrderStatus.SUBMITTED}
        )

    async def cancel_order(self, order_id: str) -> bool:
        raw = await self._request(  # type: ignore[attr-defined]
            "POST", "/api/v2/spot/trade/cancel-order", body={"orderId": order_id}
        )
        return bool(raw.get("code") == "00000")

    async def modify_order(self, order_id: str, **kwargs: Any) -> Order:
        # Bitget 스팟 주문 정정(cancel-replace) 엔드포인트 존재 여부는
        # 아직 문서로 확인 못 함 — 착수 시(Demo 키 확보 후) 확정 필요.
        # 현재는 "취소 후 재주문"이 안전한 폴백이므로 여기서 직접 정정하지 않는다.
        raise NotImplementedError(
            "Bitget 스팟 주문 정정은 아직 미확인 — 취소(cancel_order) 후 재주문 사용"
        )

    async def get_order(self, order_id: str) -> Order:
        """편차: 02번 인터페이스가 order_id 하나만으로 완전한 Order를
        반환하도록 요구하지만, Bitget 응답에는 AIOS 전용 컨텍스트(strategy_id/
        strategy_version/asset_class 등)가 없다 — 거래소는 그 개념 자체를
        모른다. 여기서는 거래소가 실제로 아는 필드(상태·체결정보·가격)만
        신뢰할 수 있게 채우고, AIOS 전용 필드는 자리표시자로 둔다 — 호출부
        (Reconciliation, FD-9.6)가 기존 DB 행과 병합해 완성해야 한다."""
        raw = await self._request(  # type: ignore[attr-defined]
            "GET", "/api/v2/spot/trade/orderInfo", params={"orderId": order_id}
        )
        data = raw["data"][0] if isinstance(raw["data"], list) else raw["data"]
        status = _map_status(data.get("status", ""))
        filled_quantity = (
            Decimal(data["fillSize"])
            if "fillSize" in data
            else Decimal(data["size"]) if status == OrderStatus.FILLED else Decimal("0")
        )

        return Order(
            order_id=uuid4(),
            exchange_order_id=data["orderId"],
            client_order_id=data.get("clientOid", ""),
            strategy_id="",  # 자리표시자 — 호출부가 DB 조회로 채워야 함
            strategy_version="",
            symbol=data.get("symbol", ""),
            exchange="bitget",
            side=OrderSide(data["side"].upper()) if "side" in data else OrderSide.BUY,
            order_type=(
                OrderType(data["orderType"].upper()) if "orderType" in data else OrderType.LIMIT
            ),
            quantity=Decimal(data.get("size", "0")),
            status=status,
            filled_quantity=filled_quantity,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            asset_class=AssetClass.CRYPTO,
        )

    async def health_check(self) -> bool:
        """Watchdog이 State DB와 무관하게 호출하는 경량 응답성 확인."""
        try:
            await self.get_balance()  # type: ignore[attr-defined]
            return True
        except Exception:  # noqa: BLE001 — 헬스체크는 어떤 예외든 False로 수렴
            return False
