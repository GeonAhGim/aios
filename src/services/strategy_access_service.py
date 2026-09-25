"""13.5 — Purchased strategy execution integration (execution access authority determination).

Spec: 기능설계문서_v1.20.md#FD-13.4, Policy document 10.3-B/4.10 cross-tenant risk 2

The owner_user_id (original creator) is preserved as-is, and execution
access authority is evaluated separately — only the owner themselves, or
a buyer whose purchase record has a confirmed payment
(payment_status='CONFIRMED'), may access the strategy's FSM definition
(the detailed logic required for execution). The original FD-13.4 text
explicitly corrected "immediately after purchase completion" to
"immediately after payment confirmation (FD-18.5b)" — to close the gap
where execution authority would arise before deposit confirmation.
(Updated — during the app assembly phase, FD-18.5b was actually exposed
as /admin/payments/{purchase_id}/confirm, making the transition to
CONFIRMED a live path.)

10.3-B Black-box principle: This service queries only strategies,
strategy_purchases, and strategy_listings, and does not handle buyer-level
execution state (FD-16 responsibility) at all — there is no path for a
seller to view a buyer's execution data.

Exception (FD-13.4): Even if the seller later DELISTs the listing, access
authority for an already CONFIRMED purchase is maintained — this is
naturally guaranteed because this determination logic does not inspect
listing.status at all.
"""
from __future__ import annotations

import json
from uuid import UUID

import asyncpg
from pydantic import BaseModel


class StrategyAccessError(Exception):
    """FD-13.4 access denied — router converts to 403."""


class StrategyDefinition(BaseModel):
    strategy_id: str
    version: str
    owner_user_id: UUID
    fsm_definition: dict[str, object]


class StrategyAccessService:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def can_access(self, user_id: UUID, strategy_id: str, strategy_version: str) -> bool:
        async with self._pool.acquire() as conn:
            owner_user_id = await conn.fetchval(
                "SELECT owner_user_id FROM strategies WHERE strategy_id = $1 AND version = $2",
                strategy_id,
                strategy_version,
            )
            if owner_user_id is None:
                return False
            if owner_user_id == user_id:
                return True

            confirmed_purchase = await conn.fetchval(
                """
                SELECT 1 FROM strategy_purchases p
                JOIN strategy_listings l ON l.id = p.listing_id
                WHERE l.strategy_id = $1 AND l.strategy_version = $2
                    AND p.buyer_user_id = $3 AND p.payment_status = 'CONFIRMED'
                LIMIT 1
                """,
                strategy_id,
                strategy_version,
                user_id,
            )
        return confirmed_purchase is not None

    async def get_strategy_for_execution(
        self, user_id: UUID, strategy_id: str, strategy_version: str
    ) -> StrategyDefinition:
        if not await self.can_access(user_id, strategy_id, strategy_version):
            raise StrategyAccessError("이 전략에 접근할 권한이 없습니다.")

        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT strategy_id, version, owner_user_id, fsm_definition FROM strategies "
                "WHERE strategy_id = $1 AND version = $2",
                strategy_id,
                strategy_version,
            )
        return StrategyDefinition(
            strategy_id=row["strategy_id"],
            version=row["version"],
            owner_user_id=row["owner_user_id"],
            fsm_definition=json.loads(row["fsm_definition"]),
        )
