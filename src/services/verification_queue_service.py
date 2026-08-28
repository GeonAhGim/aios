"""18.1 — 검증 대기열 조회 (VerificationQueueService).

Spec: 기능설계문서_v1.20.md#FD-18.1, FD-13.2, 15번 문서 §15.6

15번 문서 §15.6 이해상충 규칙 — 검증담당자 본인이 판매자인 리스팅은
대기열에서 제외한다(verifier_user_id != listing.seller_user_id). 대기중인
리스팅이 없거나 전부 본인 리스팅이라 걸러진 경우나 결과는 동일하게 빈
목록이다 — "본인 리스팅은 다른 검증담당자가 처리해야 합니다" 안내는
프론트엔드가 빈 목록에 대해 보여줄 문구이므로 여기서 별도 필드로
구분하지 않는다(빈 목록 자체가 완료조건이 요구하는 결과).
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

import asyncpg
from pydantic import BaseModel


class QueuedListing(BaseModel):
    listing_id: int
    strategy_id: str
    strategy_version: str
    seller_user_id: UUID
    price: Decimal | None
    submitted_at: datetime


class VerificationQueueService:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def list_pending(self, verifier_user_id: UUID) -> list[QueuedListing]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, strategy_id, strategy_version, seller_user_id, price, created_at "
                "FROM strategy_listings "
                "WHERE status = 'PENDING_VERIFICATION' AND seller_user_id != $1 "
                "ORDER BY created_at ASC",
                verifier_user_id,
            )
        return [
            QueuedListing(
                listing_id=row["id"],
                strategy_id=row["strategy_id"],
                strategy_version=row["strategy_version"],
                seller_user_id=row["seller_user_id"],
                price=row["price"],
                submitted_at=row["created_at"],
            )
            for row in rows
        ]
