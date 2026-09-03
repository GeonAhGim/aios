"""02b_bitget_api_v2_full_spec_v1.md §3.2(P1) — BitgetAdapter Spot Plan 주문
메서드군 + health_check().

Spec: 02_exchange_adapter_v1.3.md#§2.1, 02b_bitget_api_v2_full_spec_v1.md#§3.2

엔드포인트(2026-08-28 문서 조사 확인, 라이브 검증 필요):
- POST /api/v2/spot/trade/place-plan-order
- POST /api/v2/spot/trade/cancel-plan-order
- GET  /api/v2/spot/trade/current-plan-order

02b 스펙 §2 "인터페이스 계약 불변" 원칙 — `ExchangeAdapter` 추상 인터페이스에
아직 없다(어떤 FD-4/8 호출부도 아직 소비하지 않음, 17.9-A 과잉설계 방지).

2026-09-03 task-1032(PLT-40a 선행) — `trading_mixin.py`(314줄, P6
line_cap 초과)에서 순수 이동(동작 변경 0).
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from src.data.models.trading import OrderSide, OrderType
from src.exchanges.bitget.symbols import to_bitget_symbol as _to_bitget_symbol


class BitgetTradingPlanMixin:
    async def place_plan_order(
        self,
        symbol: str,
        side: OrderSide,
        size: Decimal,
        trigger_price: Decimal,
        *,
        order_price: Decimal | None = None,
        order_type: OrderType = OrderType.LIMIT,
        plan_type: str = "normal_plan",
    ) -> dict[str, Any]:
        """02b 스펙 §3.2(P1) — FD-8.1 stop_loss/take_profit을 폴링 대신
        거래소 네이티브 트리거로 이관할 후보. `Order` 모델에는 트리거가격
        개념이 없다(§2 모델 재사용 원칙 — 실제 소비하는 FD-8 호출부가
        생기기 전까지 새 필드 추가를 보류) — `get_fills()`와 동일하게 raw
        dict를 반환한다."""
        body: dict[str, Any] = {
            "symbol": _to_bitget_symbol(symbol),
            "side": side.value.lower(),
            "orderType": order_type.value.lower(),
            "size": str(size),
            "triggerPrice": str(trigger_price),
            "planType": plan_type,
            "force": "gtc",
        }
        if order_price is not None:
            body["executePrice"] = str(order_price)
        raw = await self._request(  # type: ignore[attr-defined]
            "POST", "/api/v2/spot/trade/place-plan-order", body=body
        )
        return dict(raw["data"])

    async def cancel_plan_order(self, order_id: str) -> bool:
        """02b 스펙 §3.2(P1)."""
        raw = await self._request(  # type: ignore[attr-defined]
            "POST", "/api/v2/spot/trade/cancel-plan-order", body={"orderId": order_id}
        )
        return bool(raw.get("code") == "00000")

    async def get_current_plan_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
        """02b 스펙 §3.2(P1)."""
        params: dict[str, Any] = {}
        if symbol is not None:
            params["symbol"] = _to_bitget_symbol(symbol)
        raw = await self._request(  # type: ignore[attr-defined]
            "GET", "/api/v2/spot/trade/current-plan-order", params=params or None
        )
        return list(raw["data"])

    async def health_check(self) -> bool:
        """Watchdog이 State DB와 무관하게 호출하는 경량 응답성 확인."""
        try:
            await self.get_balance()  # type: ignore[attr-defined]
            return True
        except Exception:  # noqa: BLE001 — 헬스체크는 어떤 예외든 False로 수렴
            return False
