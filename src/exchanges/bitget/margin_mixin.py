"""02b_bitget_api_v2_full_spec_v1.md §4 — BitgetAdapter Margin(마진) 메서드군.

Spec: 02b_bitget_api_v2_full_spec_v1.md §4(P0), §9(작업 분해 2번)

Bitget V2는 cross/isolated를 `marginType`(`crossed`|`isolated`) 경로
세그먼트 하나로 통일했다 — `bitget_ex`(작년 V1 코드)처럼 두 클래스로
나눌 필요가 없다(02b §4 원문: "코드 중복 감소"). `ExchangeAdapter` ABC에는
아직 없는 Bitget 전용 확장 메서드다(trading_mixin.py 모듈 docstring과
동일 원칙 — 소비하는 FD-4/8 호출부가 생기기 전까지 ABC로 승격하지 않음).

엔드포인트(공식 문서 미확인, 커뮤니티 SDK 레퍼런스 기준 — 실제 응답
필드명은 Demo 키 확보 후 라이브 검증 필요):
- GET  /api/v2/margin/{marginType}/account/assets
- GET  /api/v2/margin/{marginType}/account/risk-rate
- POST /api/v2/margin/{marginType}/place-order
- POST /api/v2/margin/{marginType}/cancel-order
- GET  /api/v2/margin/{marginType}/open-orders
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import uuid4

from src.data.models.base import AssetClass
from src.data.models.trading import MarginAccountAsset, Order, OrderSide, OrderStatus, OrderType

CROSSED = "crossed"
ISOLATED = "isolated"
_VALID_MARGIN_TYPES = (CROSSED, ISOLATED)


def _validate_margin_type(margin_type: str) -> None:
    if margin_type not in _VALID_MARGIN_TYPES:
        raise ValueError(
            f"알 수 없는 margin_type입니다: {margin_type!r} "
            f"(허용값: {', '.join(_VALID_MARGIN_TYPES)})"
        )


class BitgetMarginMixin:
    async def get_margin_account_assets(
        self, margin_type: str, *, symbol: str | None = None
    ) -> list[MarginAccountAsset]:
        _validate_margin_type(margin_type)
        params: dict[str, Any] = {}
        if symbol is not None:
            params["symbol"] = symbol.replace("/", "")
        raw = await self._request(  # type: ignore[attr-defined]
            "GET", f"/api/v2/margin/{margin_type}/account/assets", params=params or None
        )
        return [
            MarginAccountAsset(
                exchange="bitget",
                margin_type=margin_type,
                symbol=item.get("symbol"),
                coin=item["coin"].upper(),
                available=Decimal(item["available"]),
                borrowed=Decimal(item.get("borrow", "0")),
                interest=Decimal(item.get("interest", "0")),
                net_asset=Decimal(item.get("netAsset", item["available"])),
                risk_rate=(
                    Decimal(item["riskRate"]) if item.get("riskRate") is not None else None
                ),
            )
            for item in raw["data"]
        ]

    async def get_margin_risk_rate(
        self, margin_type: str, *, symbol: str | None = None
    ) -> Decimal:
        """FD-8.3 청산위험 판단 입력값 후보(02b §4) — 단일 스칼라로 축약해
        반환한다(cross는 계좌 전체 하나, isolated는 symbol별 하나)."""
        _validate_margin_type(margin_type)
        params: dict[str, Any] = {}
        if symbol is not None:
            params["symbol"] = symbol.replace("/", "")
        raw = await self._request(  # type: ignore[attr-defined]
            "GET", f"/api/v2/margin/{margin_type}/account/risk-rate", params=params or None
        )
        data = raw["data"][0] if isinstance(raw["data"], list) else raw["data"]
        return Decimal(data["riskRate"])

    async def place_margin_order(self, margin_type: str, order: Order) -> Order:
        _validate_margin_type(margin_type)
        body: dict[str, Any] = {
            "symbol": order.symbol.replace("/", ""),
            "side": order.side.value.lower(),
            "orderType": order.order_type.value.lower(),
            "force": "gtc",
            "baseSize": str(order.quantity),
            "clientOid": order.client_order_id,
        }
        if order.price is not None:
            body["price"] = str(order.price.amount)

        raw = await self._request(  # type: ignore[attr-defined]
            "POST", f"/api/v2/margin/{margin_type}/place-order", body=body
        )
        data = raw["data"]
        return order.model_copy(
            update={"exchange_order_id": data["orderId"], "status": OrderStatus.SUBMITTED}
        )

    async def cancel_margin_order(self, margin_type: str, order_id: str) -> bool:
        _validate_margin_type(margin_type)
        raw = await self._request(  # type: ignore[attr-defined]
            "POST", f"/api/v2/margin/{margin_type}/cancel-order", body={"orderId": order_id}
        )
        return bool(raw.get("code") == "00000")

    async def get_margin_open_orders(
        self, margin_type: str, *, symbol: str | None = None
    ) -> list[Order]:
        _validate_margin_type(margin_type)
        params: dict[str, Any] = {}
        if symbol is not None:
            params["symbol"] = symbol.replace("/", "")
        raw = await self._request(  # type: ignore[attr-defined]
            "GET", f"/api/v2/margin/{margin_type}/open-orders", params=params or None
        )
        orders = []
        for item in raw["data"]:
            status = (
                OrderStatus.PARTIALLY_FILLED
                if item.get("status") == "partially_filled"
                else OrderStatus.ACKNOWLEDGED
            )
            orders.append(
                Order(
                    order_id=uuid4(),
                    exchange_order_id=item["orderId"],
                    client_order_id=item.get("clientOid", ""),
                    strategy_id="",
                    strategy_version="",
                    symbol=item.get("symbol", ""),
                    exchange="bitget",
                    side=OrderSide(item["side"].upper()) if "side" in item else OrderSide.BUY,
                    order_type=(
                        OrderType(item["orderType"].upper())
                        if "orderType" in item
                        else OrderType.LIMIT
                    ),
                    quantity=Decimal(item.get("baseSize", item.get("size", "0"))),
                    status=status,
                    asset_class=AssetClass.CRYPTO,
                )
            )
        return orders
