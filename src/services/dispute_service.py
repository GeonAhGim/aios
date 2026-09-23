"""13.10 — Dispute submission API.

Spec: 기능설계문서_v1.20.md#FD-13.10, document #14 §14.5.1

FD-18.2 (operator dispute query/processing) only queries and processes tickets
created by this API; ticket creation itself is a separate feature — this module
fills in the "buyer raises a dispute" requirement originally specified in
document #14 but previously omitted.

Dispute submission for another buyer's purchase is blocked (purchase ownership
check); at most one OPEN dispute per purchase — the DB partial unique index
(idx_disputes_open_per_purchase) is the final defense line.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

import asyncpg
from pydantic import BaseModel


class DisputeError(Exception):
    """FD-13.10 failure — router translates to 400/403/404."""


class Dispute(BaseModel):
    id: int
    purchase_id: int
    submitted_by: UUID
    reason: str
    status: str
    created_at: datetime


class DisputeService:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def submit(self, submitted_by: UUID, purchase_id: int, reason: str) -> Dispute:
        if not reason.strip():
            raise DisputeError("사유(reason)를 입력해야 합니다.")

        async with self._pool.acquire() as conn:
            purchase = await conn.fetchrow(
                "SELECT buyer_user_id FROM strategy_purchases WHERE id = $1", purchase_id
            )
            if purchase is None:
                raise DisputeError("존재하지 않는 구매 건입니다.")
            if purchase["buyer_user_id"] != submitted_by:
                raise DisputeError("본인의 구매 건에 대해서만 분쟁을 제기할 수 있습니다.")

            try:
                row = await conn.fetchrow(
                    "INSERT INTO disputes (purchase_id, submitted_by, reason) "
                    "VALUES ($1, $2, $3) RETURNING id, purchase_id, submitted_by, reason, "
                    "status, created_at",
                    purchase_id,
                    submitted_by,
                    reason,
                )
            except asyncpg.UniqueViolationError as exc:
                raise DisputeError(
                    "이미 이 구매 건에 대해 진행중인 분쟁이 있습니다."
                ) from exc

        return Dispute(**dict(row))
