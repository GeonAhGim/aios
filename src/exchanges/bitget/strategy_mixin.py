"""02c_bitget_api_v2_extended_spec_v1.md §1.10 — BitgetAdapter Strategy(전략주문) 메서드군.

Spec: 02c_bitget_api_v2_extended_spec_v1.md §1.10, §2(작업 분해 7번)

Bitget이 서버측에서 관리하는 고급 주문 타입(아이스버그/TWAP 등) —
Spot의 Plan(Trigger) 주문(trading_mixin.py::place_plan_order)과는 다른
네임스페이스(`/api/v2/trace/strategy/*`). `ExchangeAdapter` ABC에는
아직 없음. 엔드포인트(커뮤니티 SDK 레퍼런스 기준, 라이브 검증 필요):
- POST /api/v2/trace/strategy/place-order
- POST /api/v2/trace/strategy/cancel-order
- GET  /api/v2/trace/strategy/current-order
- GET  /api/v2/trace/strategy/history-order
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from src.exchanges.bitget.symbols import to_bitget_symbol as _to_bitget_symbol
from src.exchanges.common.live_guard import require_paper_sandbox


class BitgetStrategyMixin:
    @require_paper_sandbox
    async def place_strategy_order(
        self,
        symbol: str,
        side: str,
        strategy_type: str,
        total_amount: Decimal,
        *,
        price: Decimal | None = None,
        duration_seconds: int | None = None,
    ) -> dict[str, Any]:
        """`strategy_type`은 Bitget 문서값 그대로("iceberg"|"twap" 등,
        라이브 검증 필요). Plan/TPSL 주문과 마찬가지로 `Order` 모델과
        형태가 달라(전략 파라미터 위주) raw dict를 반환한다.

        레드팀 #2026-09-02-32/33 — Executor를 거치지 않으므로 최소
        방어선을 이 메서드 자체에 건다."""
        if total_amount <= 0:
            raise ValueError("total_amount는 0보다 커야 합니다.")
        if price is not None and price <= 0:
            raise ValueError("price는 0보다 커야 합니다.")
        if duration_seconds is not None and duration_seconds <= 0:
            raise ValueError("duration_seconds는 0보다 커야 합니다.")
        body: dict[str, Any] = {
            "symbol": _to_bitget_symbol(symbol),
            "side": side.lower(),
            "strategyType": strategy_type,
            "totalAmount": str(total_amount),
        }
        if price is not None:
            body["price"] = str(price)
        if duration_seconds is not None:
            body["duration"] = str(duration_seconds)
        raw = await self._request(  # type: ignore[attr-defined]
            "POST", "/api/v2/trace/strategy/place-order", body=body
        )
        return dict(raw["data"])

    async def cancel_strategy_order(self, order_id: str, *, symbol: str) -> bool:
        raw = await self._request(  # type: ignore[attr-defined]
            "POST",
            "/api/v2/trace/strategy/cancel-order",
            body={"orderId": order_id, "symbol": _to_bitget_symbol(symbol)},
        )
        return bool(raw.get("code") == "00000")

    async def get_current_strategy_orders(
        self, *, symbol: str | None = None
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {}
        if symbol is not None:
            params["symbol"] = _to_bitget_symbol(symbol)
        raw = await self._request(  # type: ignore[attr-defined]
            "GET", "/api/v2/trace/strategy/current-order", params=params or None
        )
        return list(raw["data"])

    async def get_strategy_order_history(
        self, *, symbol: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": str(limit)}
        if symbol is not None:
            params["symbol"] = _to_bitget_symbol(symbol)
        raw = await self._request(  # type: ignore[attr-defined]
            "GET", "/api/v2/trace/strategy/history-order", params=params
        )
        return list(raw["data"])
