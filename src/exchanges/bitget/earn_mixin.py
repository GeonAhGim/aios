"""02c_bitget_api_v2_extended_spec_v1.md §1.4 — BitgetAdapter Earn(적금/이자상품) 메서드군.

Spec: 02c_bitget_api_v2_extended_spec_v1.md §1.4, §2(작업 분해 4번)

예치형 이자상품(트레이딩이 아닌 자산운용) — 해지(redeem)는 "출금"이
아니라 상품 해지일 뿐이며 자산은 계정 내부에 남는다(7.9 원칙 무관).
`ExchangeAdapter` ABC에는 아직 없음(다른 확장 메서드들과 동일 원칙).
엔드포인트(커뮤니티 SDK 레퍼런스 기준, 라이브 검증 필요):
- GET  /api/v2/earn/savings/product
- POST /api/v2/earn/savings/subscribe
- POST /api/v2/earn/savings/redeem
- GET  /api/v2/earn/savings/assets
- GET  /api/v2/earn/savings/records
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any


class BitgetEarnMixin:
    async def get_earn_products(self, *, coin: str | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {}
        if coin is not None:
            params["coin"] = coin.upper()
        raw = await self._request(  # type: ignore[attr-defined]
            "GET", "/api/v2/earn/savings/product", params=params or None
        )
        return list(raw["data"])

    async def subscribe_earn_product(
        self, product_id: str, amount: Decimal, *, period_type: str = "flexible"
    ) -> dict[str, Any]:
        raw = await self._request(  # type: ignore[attr-defined]
            "POST",
            "/api/v2/earn/savings/subscribe",
            body={
                "productId": product_id,
                "amount": str(amount),
                "periodType": period_type,
            },
        )
        return dict(raw["data"])

    async def redeem_earn_product(self, order_id: str, amount: Decimal) -> dict[str, Any]:
        """상품 해지(상환) — 계정 내부 자산 재배치일 뿐 외부 출금이
        아니다(7.9 원칙 무관, §0 모듈 docstring 참조)."""
        raw = await self._request(  # type: ignore[attr-defined]
            "POST",
            "/api/v2/earn/savings/redeem",
            body={"orderId": order_id, "amount": str(amount)},
        )
        return dict(raw["data"])

    async def get_earn_assets(self) -> list[dict[str, Any]]:
        raw = await self._request("GET", "/api/v2/earn/savings/assets")  # type: ignore[attr-defined]
        return list(raw["data"])

    async def get_earn_records(
        self, *, coin: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": str(limit)}
        if coin is not None:
            params["coin"] = coin.upper()
        raw = await self._request(  # type: ignore[attr-defined]
            "GET", "/api/v2/earn/savings/records", params=params
        )
        return list(raw["data"])
