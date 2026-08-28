"""18.4 — 판매자 정지 처리 (SellerSuspensionService).

Spec: 기능설계문서_v1.20.md#FD-18.4, 14번 문서 §14.5.3, 정책문서 8.10

users.seller_suspended를 토글한다 — 정지 시 신규 리스팅 생성이 거부된다
(ListingService.create_listing이 이 플래그를 확인). 이미 정지된 판매자를
재정지 시도해도 멱등 처리(에러 아님, 현재 상태 그대로 반환).

reason은 사용자에게는 비공개(분쟁 사유가 민감할 수 있음)지만 내부
기록용이다 — 8.10 원칙에 따라 audit_log에 남긴다(18.2와 동일하게
record_audit_log 재사용).
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

import asyncpg
from pydantic import BaseModel

from src.core.logging.audit_log import record_audit_log


class SellerSuspensionError(Exception):
    """FD-18.4 실패 — 라우터가 404로 변환."""


class SellerSuspensionResult(BaseModel):
    user_id: UUID
    seller_suspended: bool
    suspended_at: datetime


class SellerSuspensionService:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def suspend(
        self, user_id: UUID, admin_user_id: UUID, reason: str
    ) -> SellerSuspensionResult:
        async with self._pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                "UPDATE users SET seller_suspended = true WHERE user_id = $1 "
                "RETURNING seller_suspended",
                user_id,
            )
            if row is None:
                raise SellerSuspensionError("존재하지 않는 사용자입니다.")

            await record_audit_log(
                conn,
                actor_agent=str(admin_user_id),
                action_type="seller.suspended",
                decision_data={"target_user_id": str(user_id), "reason": reason},
                target_type="user",
                target_id=str(user_id),
            )

        return SellerSuspensionResult(
            user_id=user_id,
            seller_suspended=row["seller_suspended"],
            suspended_at=datetime.now(timezone.utc),
        )
