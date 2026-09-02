"""02c_bitget_api_v2_extended_spec_v1.md §1.9 — BitgetAdapter Grid(그리드봇) 메서드군.

Spec: 02c_bitget_api_v2_extended_spec_v1.md §1.9, §2(작업 분해 6번)

거래소가 대신 실행하는 자동 매매 전략(등간격 매수/매도 그리드) — AIOS의
FD-8 전략 엔진과 개념적으로 경쟁 관계이지만, API 연동 자체는 요청
범위이므로 제공한다(언제 쓸지는 FD-8 판단, 17.9-A와 동일 원칙).
`ExchangeAdapter` ABC에는 아직 없음. 엔드포인트(커뮤니티 SDK 레퍼런스
기준, 라이브 검증 필요):
- POST /api/v2/spot/grid/place-grid
- POST /api/v2/mix/grid/place-grid
- POST /api/v2/spot/grid/close-grid
- GET  /api/v2/spot/grid/current-grid
- GET  /api/v2/spot/grid/grid-history
- GET  /api/v2/spot/grid/grid-profit
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from src.exchanges.bitget.symbols import to_bitget_symbol as _to_bitget_symbol
from src.exchanges.common.live_guard import require_paper_sandbox


def _validate_grid_params(
    *, lower_price: Decimal, upper_price: Decimal, grid_count: int, investment: Decimal
) -> None:
    """레드팀 #2026-09-02-33 — Executor를 거치지 않으므로 최소 sanity
    check를 이 메서드 자체에 건다."""
    if lower_price <= 0 or upper_price <= 0:
        raise ValueError("lower_price/upper_price는 0보다 커야 합니다.")
    if lower_price >= upper_price:
        raise ValueError("lower_price는 upper_price보다 작아야 합니다.")
    if grid_count <= 0:
        raise ValueError("grid_count는 0보다 커야 합니다.")
    if investment <= 0:
        raise ValueError("investment는 0보다 커야 합니다.")


class BitgetGridMixin:
    @require_paper_sandbox
    async def place_spot_grid(
        self,
        symbol: str,
        lower_price: Decimal,
        upper_price: Decimal,
        grid_count: int,
        investment: Decimal,
        *,
        run_type: str = "arithmetic",
    ) -> dict[str, Any]:
        """`run_type`은 "arithmetic"(등차)/"geometric"(등비) 문서 관례."""
        _validate_grid_params(
            lower_price=lower_price,
            upper_price=upper_price,
            grid_count=grid_count,
            investment=investment,
        )
        raw = await self._request(  # type: ignore[attr-defined]
            "POST",
            "/api/v2/spot/grid/place-grid",
            body={
                "symbol": _to_bitget_symbol(symbol),
                "lowerLimit": str(lower_price),
                "upperLimit": str(upper_price),
                "gridNum": str(grid_count),
                "investment": str(investment),
                "runType": run_type,
            },
        )
        return dict(raw["data"])

    @require_paper_sandbox
    async def place_futures_grid(
        self,
        symbol: str,
        lower_price: Decimal,
        upper_price: Decimal,
        grid_count: int,
        investment: Decimal,
        *,
        product_type: str = "USDT-FUTURES",
        run_type: str = "arithmetic",
    ) -> dict[str, Any]:
        _validate_grid_params(
            lower_price=lower_price,
            upper_price=upper_price,
            grid_count=grid_count,
            investment=investment,
        )
        raw = await self._request(  # type: ignore[attr-defined]
            "POST",
            "/api/v2/mix/grid/place-grid",
            body={
                "symbol": _to_bitget_symbol(symbol),
                "productType": product_type,
                "lowerLimit": str(lower_price),
                "upperLimit": str(upper_price),
                "gridNum": str(grid_count),
                "investment": str(investment),
                "runType": run_type,
            },
        )
        return dict(raw["data"])

    async def close_grid(self, grid_id: str) -> bool:
        raw = await self._request(  # type: ignore[attr-defined]
            "POST", "/api/v2/spot/grid/close-grid", body={"gridId": grid_id}
        )
        return bool(raw.get("code") == "00000")

    async def get_current_grids(self, *, symbol: str | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {}
        if symbol is not None:
            params["symbol"] = _to_bitget_symbol(symbol)
        raw = await self._request(  # type: ignore[attr-defined]
            "GET", "/api/v2/spot/grid/current-grid", params=params or None
        )
        return list(raw["data"])

    async def get_grid_history(self, *, symbol: str | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {}
        if symbol is not None:
            params["symbol"] = _to_bitget_symbol(symbol)
        raw = await self._request(  # type: ignore[attr-defined]
            "GET", "/api/v2/spot/grid/grid-history", params=params or None
        )
        return list(raw["data"])

    async def get_grid_profit(self, grid_id: str) -> dict[str, Any]:
        raw = await self._request(  # type: ignore[attr-defined]
            "GET", "/api/v2/spot/grid/grid-profit", params={"gridId": grid_id}
        )
        data = raw["data"][0] if isinstance(raw["data"], list) else raw["data"]
        return dict(data)
