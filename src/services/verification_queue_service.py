"""18.1 — Verification queue listing (VerificationQueueService).

Spec: functional_design_doc_v1.20.md#FD-18.1, FD-13.2, Document-15 §15.6

Conflict-of-interest rule (Document-15 §15.6) — listings where the
verifier is the seller are excluded from the queue
(verifier_user_id != listing.seller_user_id). If there are no pending
listings or all are filtered out as self-listings, the result is an
empty list either way. The message "Self-listings must be handled by
another verifier" is UI text shown by the frontend for an empty list,
so there is no separate field to distinguish this here (an empty list
itself is the completion condition).
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
