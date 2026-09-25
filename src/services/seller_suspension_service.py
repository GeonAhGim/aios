"""18.4 — Seller suspension handling (SellerSuspensionService).

Spec: 기능설계문서_v1.20.md#FD-18.4, 14번 문서 §14.5.3, 정책문서 8.10

Toggles users.seller_suspended — when suspended, new listing creation
is rejected (ListingService.create_listing checks this flag). Attempting
to re-suspend an already-suspended seller is idempotent (not an error;
returns the current state unchanged).

The reason is private to end users (dispute reasons may be sensitive)
but recorded internally — written to audit_log per 8.10 principle
(reusing record_audit_log, same as 18.2).
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

import asyncpg
from pydantic import BaseModel

from src.core.logging.audit_log import record_audit_log


class SellerSuspensionError(Exception):
    """FD-18.4 failure — router converts to 404."""


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
