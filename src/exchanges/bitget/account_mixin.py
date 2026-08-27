"""6.6 — BitgetAdapter Account 메서드군(get_balance/get_positions).

Spec: 02_exchange_adapter_v1.2.md#§2.1

엔드포인트: GET /api/v2/spot/account/assets (2026-08-28 문서 조사 확인 —
실제 응답은 Demo API 키로 라이브 검증 필요, .env BITGET_API_KEY 채워지면
최우선 검증 대상).
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from src.data.models.trading import AccountBalance, Position


class BitgetAccountMixin:
    async def get_balance(self, asset: str | None = None) -> list[AccountBalance]:
        params: dict[str, Any] | None = {"coin": asset} if asset else None
        raw = await self._request(  # type: ignore[attr-defined]
            "GET", "/api/v2/spot/account/assets", params=params
        )
        balances = []
        for item in raw["data"]:
            available = Decimal(item["available"])
            frozen = Decimal(item["frozen"])
            locked = Decimal(item.get("locked", "0"))
            balances.append(
                AccountBalance(
                    exchange="bitget",
                    asset=item["coin"].upper(),
                    total=available + frozen + locked,
                    available=available,
                    used_margin=frozen + locked,
                )
            )
        return balances

    async def get_positions(self, symbol: str | None = None) -> list[Position]:
        """스팟 거래소는 선물/마진과 달리 네이티브 "포지션" 개념이 없다 —
        AIOS 자체가 체결 내역으로부터 포지션을 내부적으로 계산·추적하고,
        Reconciliation(FD-9.6)은 get_balance()를 거래소측 진실 소스로
        사용한다. Phase 1은 Bitget spot만 대상(06번 §6.1)이므로 항상
        빈 리스트를 반환한다."""
        return []
