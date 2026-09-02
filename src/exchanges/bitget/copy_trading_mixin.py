"""02c_bitget_api_v2_extended_spec_v1.md §1.8 — BitgetAdapter Copy Trading(카피트레이딩) 메서드군.

Spec: 02c_bitget_api_v2_extended_spec_v1.md §1.8, §2(작업 분해 10번)

Bitget 자체 카피트레이딩 마켓플레이스 — 트레이더(팔로우 대상)와
팔로워(따라가는 쪽) 양쪽 역할의 API가 다르다. AIOS 자체 전략 실행
(FD-8)과 무관(17.9-A) — API 연동만 제공한다. `ExchangeAdapter` ABC에는
아직 없음. 엔드포인트(커뮤니티 SDK 레퍼런스 기준, 라이브 검증 필요):
- GET  /api/v2/copy/mix-follower/query-traders
- POST /api/v2/copy/mix-follower/setting
- POST /api/v2/copy/mix-follower/close-settings
- GET  /api/v2/copy/mix-follower/query-current-orders
- GET  /api/v2/copy/mix-follower/query-history-orders
- GET  /api/v2/copy/mix-trader/config-query-followers
- POST /api/v2/copy/mix-trader/config-settings-base
- GET  /api/v2/copy/mix-trader/order-profit-history-summary
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any


class BitgetCopyTradingMixin:
    # ---------- 팔로워(따라가는 쪽) ----------

    async def get_copy_trading_traders(self, *, limit: int = 100) -> list[dict[str, Any]]:
        """내가 팔로우 중인 트레이더 목록."""
        raw = await self._request(  # type: ignore[attr-defined]
            "GET", "/api/v2/copy/mix-follower/query-traders", params={"limit": str(limit)}
        )
        return list(raw["data"])

    async def follow_copy_trader(
        self, trader_id: str, *, copy_amount: Decimal | None = None, margin_coin: str = "USDT"
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"traderId": trader_id, "marginCoin": margin_coin}
        if copy_amount is not None:
            body["copyAmount"] = str(copy_amount)
        raw = await self._request(  # type: ignore[attr-defined]
            "POST", "/api/v2/copy/mix-follower/setting", body=body
        )
        return dict(raw["data"])

    async def unfollow_copy_trader(self, trader_id: str) -> bool:
        raw = await self._request(  # type: ignore[attr-defined]
            "POST", "/api/v2/copy/mix-follower/close-settings", body={"traderId": trader_id}
        )
        return bool(raw.get("code") == "00000")

    async def get_copy_trading_current_orders(
        self, *, trader_id: str | None = None
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {}
        if trader_id is not None:
            params["traderId"] = trader_id
        raw = await self._request(  # type: ignore[attr-defined]
            "GET", "/api/v2/copy/mix-follower/query-current-orders", params=params or None
        )
        return list(raw["data"])

    async def get_copy_trading_order_history(
        self, *, trader_id: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": str(limit)}
        if trader_id is not None:
            params["traderId"] = trader_id
        raw = await self._request(  # type: ignore[attr-defined]
            "GET", "/api/v2/copy/mix-follower/query-history-orders", params=params
        )
        return list(raw["data"])

    # ---------- 트레이더(팔로우 대상) ----------

    async def get_copy_trading_followers(self, *, limit: int = 100) -> list[dict[str, Any]]:
        """내가 트레이더 역할일 때 — 나를 팔로우 중인 사용자 목록."""
        raw = await self._request(  # type: ignore[attr-defined]
            "GET", "/api/v2/copy/mix-trader/config-query-followers", params={"limit": str(limit)}
        )
        return list(raw["data"])

    async def update_copy_trading_trader_profile(self, **settings: Any) -> dict[str, Any]:
        """트레이더 프로필 설정(수익 배분율 등, 필드가 문서상 다양해
        `**settings`로 그대로 전달 — 소비하는 호출부가 생기면 그때
        명시적 파라미터로 좁힌다)."""
        raw = await self._request(  # type: ignore[attr-defined]
            "POST", "/api/v2/copy/mix-trader/config-settings-base", body=dict(settings)
        )
        return dict(raw["data"])

    async def get_copy_trading_profit_summary(self) -> dict[str, Any]:
        raw = await self._request(  # type: ignore[attr-defined]
            "GET", "/api/v2/copy/mix-trader/order-profit-history-summary"
        )
        return dict(raw["data"])
