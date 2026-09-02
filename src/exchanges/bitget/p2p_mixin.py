"""02c_bitget_api_v2_extended_spec_v1.md §1.3 — BitgetAdapter P2P(개인간 법정화폐 거래) 메서드군.

Spec: 02c_bitget_api_v2_extended_spec_v1.md §1.3, §2(작업 분해 8번)

개인간 법정화폐 거래 — 트레이딩 엔진 도메인 밖이지만 사용자 요청("모든
기능")에 따라 API 연동만 제공한다. `ExchangeAdapter` ABC에는 아직 없음.
엔드포인트(커뮤니티 SDK 레퍼런스 기준, 라이브 검증 필요):
- GET /api/v2/p2p/advList
- GET /api/v2/p2p/merchantInfo
- GET /api/v2/p2p/orderList
- GET /api/v2/p2p/merchantList
"""
from __future__ import annotations

from typing import Any


class BitgetP2PMixin:
    async def get_p2p_ads(self, *, coin: str | None = None) -> list[dict[str, Any]]:
        """내가 올린 P2P 광고(주문) 목록."""
        params: dict[str, Any] = {}
        if coin is not None:
            params["coin"] = coin.upper()
        raw = await self._request(  # type: ignore[attr-defined]
            "GET", "/api/v2/p2p/advList", params=params or None
        )
        return list(raw["data"])

    async def get_p2p_merchant_info(self) -> dict[str, Any]:
        raw = await self._request("GET", "/api/v2/p2p/merchantInfo")  # type: ignore[attr-defined]
        return dict(raw["data"])

    async def get_p2p_orders(
        self, *, status: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": str(limit)}
        if status is not None:
            params["status"] = status
        raw = await self._request(  # type: ignore[attr-defined]
            "GET", "/api/v2/p2p/orderList", params=params
        )
        return list(raw["data"])

    async def get_p2p_merchants(self, *, coin: str | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {}
        if coin is not None:
            params["coin"] = coin.upper()
        raw = await self._request(  # type: ignore[attr-defined]
            "GET", "/api/v2/p2p/merchantList", params=params or None
        )
        return list(raw["data"])
