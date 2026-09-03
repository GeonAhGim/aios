"""02b_bitget_api_v2_full_spec_v1.md §5.4(P1) — BitgetAdapter Futures Plan/TPSL 주문 메서드군.

Spec: 02b_bitget_api_v2_full_spec_v1.md §5.4, P1

엔드포인트(커뮤니티 SDK 레퍼런스 기준, 라이브 검증 필요):
- POST /api/v2/mix/order/place-tpsl-order
- POST /api/v2/mix/order/place-pos-tpsl
- POST /api/v2/mix/order/place-plan-order
- POST /api/v2/mix/order/cancel-plan-order
- GET  /api/v2/mix/order/orders-plan-pending

2026-09-03 task-1032(PLT-40a 선행) — `futures_trading_mixin.py`(357줄, P6
line_cap 초과)에서 Plan/TPSL 메서드군만 순수 이동(동작 변경 0).
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from src.data.models.trading import Order
from src.exchanges.bitget.futures_market_mixin import DEFAULT_PRODUCT_TYPE
from src.exchanges.bitget.symbols import to_bitget_symbol as _to_bitget_symbol
from src.exchanges.common.live_guard import require_paper_sandbox


class BitgetFuturesPlanMixin:
    @require_paper_sandbox
    async def place_futures_tpsl_order(
        self,
        symbol: str,
        plan_type: str,
        trigger_price: Decimal,
        *,
        size: Decimal | None = None,
        hold_side: str | None = None,
        margin_coin: str = "USDT",
        product_type: str = DEFAULT_PRODUCT_TYPE,
    ) -> dict[str, Any]:
        """02b 스펙 §5.4(P1) — FD-8.1 stop_loss/take_profit 네이티브 이관
        후보. `plan_type`은 Bitget 문서값 그대로("profit_plan"|"loss_plan"
        등, 라이브 검증 필요). `size` 생략 시 포지션 전체 청산용(문서 관례).
        Plan/TPSL 주문은 `Order` 모델과 형태가 달라(트리거가격 등) raw
        dict를 반환한다(trading_mixin.py::place_plan_order와 동일 판단).
        레드팀 #2026-09-02-32/33 참조."""
        if trigger_price <= 0:
            raise ValueError("trigger_price는 0보다 커야 합니다.")
        if size is not None and size <= 0:
            raise ValueError("size는 0보다 커야 합니다.")
        body: dict[str, Any] = {
            "symbol": _to_bitget_symbol(symbol),
            "productType": product_type,
            "marginCoin": margin_coin,
            "planType": plan_type,
            "triggerPrice": str(trigger_price),
        }
        if size is not None:
            body["size"] = str(size)
        if hold_side is not None:
            body["holdSide"] = hold_side
        raw = await self._request(  # type: ignore[attr-defined]
            "POST", "/api/v2/mix/order/place-tpsl-order", body=body
        )
        return dict(raw["data"])

    @require_paper_sandbox
    async def place_futures_position_tpsl(
        self,
        symbol: str,
        *,
        take_profit_trigger: Decimal | None = None,
        stop_loss_trigger: Decimal | None = None,
        hold_side: str | None = None,
        margin_coin: str = "USDT",
        product_type: str = DEFAULT_PRODUCT_TYPE,
    ) -> dict[str, Any]:
        """02b 스펙 §5.4(P1) — 포지션 단위 TP/SL(한 번의 호출로 익절/손절
        둘 다 또는 하나만 설정, place_futures_tpsl_order와 달리 사이즈
        분할 없이 포지션 전체에 바인딩됨). 레드팀 #2026-09-02-32/33 참조."""
        if take_profit_trigger is None and stop_loss_trigger is None:
            raise ValueError("take_profit_trigger 또는 stop_loss_trigger 중 하나는 필요합니다")
        if take_profit_trigger is not None and take_profit_trigger <= 0:
            raise ValueError("take_profit_trigger는 0보다 커야 합니다.")
        if stop_loss_trigger is not None and stop_loss_trigger <= 0:
            raise ValueError("stop_loss_trigger는 0보다 커야 합니다.")
        body: dict[str, Any] = {
            "symbol": _to_bitget_symbol(symbol),
            "productType": product_type,
            "marginCoin": margin_coin,
        }
        if take_profit_trigger is not None:
            body["stopSurplusTriggerPrice"] = str(take_profit_trigger)
        if stop_loss_trigger is not None:
            body["stopLossTriggerPrice"] = str(stop_loss_trigger)
        if hold_side is not None:
            body["holdSide"] = hold_side
        raw = await self._request(  # type: ignore[attr-defined]
            "POST", "/api/v2/mix/order/place-pos-tpsl", body=body
        )
        return dict(raw["data"])

    @require_paper_sandbox
    async def place_futures_plan_order(
        self,
        order: Order,
        trigger_price: Decimal,
        *,
        margin_coin: str = "USDT",
        margin_mode: str = "crossed",
        product_type: str = DEFAULT_PRODUCT_TYPE,
    ) -> dict[str, Any]:
        """02b 스펙 §5.4(P1) — 예약(Plan) 주문 제출. 레드팀
        #2026-09-02-32/33 참조."""
        if order.quantity <= 0:
            raise ValueError("order.quantity는 0보다 커야 합니다.")
        if trigger_price <= 0:
            raise ValueError("trigger_price는 0보다 커야 합니다.")
        body: dict[str, Any] = {
            "symbol": _to_bitget_symbol(order.symbol),
            "productType": product_type,
            "marginMode": margin_mode,
            "marginCoin": margin_coin,
            "side": order.side.value.lower(),
            "orderType": order.order_type.value.lower(),
            "size": str(order.quantity),
            "triggerPrice": str(trigger_price),
            "clientOid": order.client_order_id,
        }
        if order.price is not None:
            body["executePrice"] = str(order.price.amount)
        raw = await self._request(  # type: ignore[attr-defined]
            "POST", "/api/v2/mix/order/place-plan-order", body=body
        )
        return dict(raw["data"])

    async def cancel_futures_plan_order(
        self, order_id: str, *, symbol: str, product_type: str = DEFAULT_PRODUCT_TYPE
    ) -> bool:
        """02b 스펙 §5.4(P1)."""
        raw = await self._request(  # type: ignore[attr-defined]
            "POST",
            "/api/v2/mix/order/cancel-plan-order",
            body={
                "orderId": order_id,
                "symbol": _to_bitget_symbol(symbol),
                "productType": product_type,
            },
        )
        return bool(raw.get("code") == "00000")

    async def get_futures_current_plan_orders(
        self, *, symbol: str | None = None, product_type: str = DEFAULT_PRODUCT_TYPE
    ) -> list[dict[str, Any]]:
        """02b 스펙 §5.4(P1)."""
        params: dict[str, Any] = {"productType": product_type}
        if symbol is not None:
            params["symbol"] = _to_bitget_symbol(symbol)
        raw = await self._request(  # type: ignore[attr-defined]
            "GET", "/api/v2/mix/order/orders-plan-pending", params=params
        )
        return list(raw["data"].get("entrustedList") or [])
