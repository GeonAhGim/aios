"""13.4 — 전략 구매 API (자전거래 방지 포함).

Spec: 기능설계문서_v1.20.md#FD-13.3, 13번 §13.5

FD-15.3(위험등급 매칭 경고)이 아직 없어(FD-15 전체가 뒤 섹션) 리스크
경고 조회는 DI 콜백으로 주입받는다 — 경고가 있는데 명시적 동의가 없으면
구매를 막는다.

price_paid는 listing.price 그대로 기록한다 — 중개수수료 계산(FD-13.7)은
platform_commission_* 컬럼 자체가 아직 없어(3.13 마이그레이션이 "13.7
이후"로 명시 분리) 그 리프에서 ALTER TABLE과 함께 추가한다.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import UUID

import asyncpg
from pydantic import BaseModel

CheckRiskWarningFn = Callable[[UUID, str, str], Awaitable[str | None]]


async def _no_risk_warning(buyer_user_id: UUID, strategy_id: str, strategy_version: str) -> None:
    return None


class PurchaseError(Exception):
    """FD-13.3 실패 — 라우터가 400/403/404/409로 변환."""


class PurchaseResult(BaseModel):
    purchase_id: int
    status: str
    risk_warning: str | None = None


class PurchaseService:
    def __init__(
        self, pool: asyncpg.Pool, *, check_risk_warning: CheckRiskWarningFn = _no_risk_warning
    ) -> None:
        self._pool = pool
        self._check_risk_warning = check_risk_warning

    async def purchase(
        self,
        buyer_user_id: UUID,
        listing_id: int,
        *,
        risk_warning_acknowledged: bool = False,
    ) -> PurchaseResult:
        async with self._pool.acquire() as conn:
            listing = await conn.fetchrow(
                "SELECT * FROM strategy_listings WHERE id = $1", listing_id
            )
            if listing is None:
                raise PurchaseError("존재하지 않는 리스팅입니다.")
            if listing["status"] != "LISTED":
                raise PurchaseError(
                    f"구매할 수 없는 리스팅 상태입니다(현재: {listing['status']})."
                )
            if listing["seller_user_id"] == buyer_user_id:
                raise PurchaseError("본인이 판매 중인 전략은 구매할 수 없습니다.")

            warning = await self._check_risk_warning(
                buyer_user_id, listing["strategy_id"], listing["strategy_version"]
            )
            if warning is not None and not risk_warning_acknowledged:
                raise PurchaseError(warning)

            row = await conn.fetchrow(
                "INSERT INTO strategy_purchases (listing_id, buyer_user_id, price_paid) "
                "VALUES ($1, $2, $3) RETURNING id, payment_status",
                listing_id,
                buyer_user_id,
                listing["price"],
            )
        return PurchaseResult(
            purchase_id=row["id"],
            status=row["payment_status"],
            risk_warning=warning if risk_warning_acknowledged else None,
        )
