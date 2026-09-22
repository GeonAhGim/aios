"""18.2 — Dispute ticket read paths (list / detail).

Spec: functional_spec_v1.20.md#FD-18.2, document #14 §14.5.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg
from pydantic import BaseModel

from src.services.dispute_resolution.errors import DisputeResolutionError


class DisputeDetail(BaseModel):
    dispute_id: int
    purchase_id: int
    submitted_by: UUID
    reason: str
    status: str
    listing_id: int
    listing_status: str
    seller_user_id: UUID
    buyer_user_id: UUID
    created_at: datetime


async def list_disputes(pool: asyncpg.Pool, status: str | None = None) -> list[dict[str, Any]]:
    async with pool.acquire() as conn:
        if status is not None:
            rows = await conn.fetch(
                "SELECT * FROM disputes WHERE status = $1 ORDER BY created_at DESC", status
            )
        else:
            rows = await conn.fetch("SELECT * FROM disputes ORDER BY created_at DESC")
    return [dict(row) for row in rows]


async def get_detail(pool: asyncpg.Pool, dispute_id: int) -> DisputeDetail:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT d.id AS dispute_id, d.purchase_id, d.submitted_by, d.reason, d.status,
                   d.created_at, l.id AS listing_id, l.status AS listing_status,
                   l.seller_user_id, p.buyer_user_id
            FROM disputes d
            JOIN strategy_purchases p ON p.id = d.purchase_id
            JOIN strategy_listings l ON l.id = p.listing_id
            WHERE d.id = $1
            """,
            dispute_id,
        )
    if row is None:
        raise DisputeResolutionError("존재하지 않는 분쟁입니다.")
    return DisputeDetail(**dict(row))
