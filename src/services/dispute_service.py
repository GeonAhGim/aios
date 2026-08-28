"""13.10 — 분쟁 접수 API.

Spec: 기능설계문서_v1.20.md#FD-13.10, 14번 문서 §14.5.1

FD-18.2(운영자 분쟁 조회·처리)는 이 API가 만든 티켓을 조회·처리할 뿐,
티켓 생성 자체는 별도 기능이다 — 14번 문서가 원래 요구했으나 반영이
빠졌던 "구매자가 분쟁을 제기하는" 쪽을 여기서 채운다.

타인 구매건에 대한 분쟁 제기는 차단(구매 소유권 확인), 구매건당 진행중
(OPEN) 분쟁은 1개만 — DB의 부분 유니크 인덱스(idx_disputes_open_per_purchase)
가 최종 방어선이다.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

import asyncpg
from pydantic import BaseModel


class DisputeError(Exception):
    """FD-13.10 실패 — 라우터가 400/403/404로 변환."""


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
