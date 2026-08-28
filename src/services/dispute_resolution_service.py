"""18.2 — 분쟁 티켓 조회·처리 (DisputeResolutionService).

Spec: 기능설계문서_v1.20.md#FD-18.2, 14번 문서 §14.5, 8.10

처리 결정은 두 갈래뿐이다 — "정상 리스크 실현으로 종결"(리스팅 상태
불변)과 "DELISTED + 환불 처리"(리스팅을 DELISTED로 전환). PG 미연동
Phase 1 전제라 실제 환불 자금 이동은 스콥 밖 — 여기서는 리스팅 상태
전환까지만 다룬다.

금전/신뢰 관련 운영자 판단이라 8.10 원칙에 따라 반드시 audit_log에
기록한다(FD-7.2 record_audit_log 재사용 — 이 프로젝트에서 최초 실호출
지점).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg
from pydantic import BaseModel

from src.core.logging.audit_log import record_audit_log

VALID_DECISIONS = ("NORMAL_RISK_REALIZATION", "DELISTED_AND_REFUND")


class DisputeResolutionError(Exception):
    """FD-18.2 실패 — 라우터가 400/404로 변환."""


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


class DisputeResolutionResult(BaseModel):
    dispute_id: int
    listing_status: str
    resolved_at: datetime


class DisputeResolutionService:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def list_disputes(self, status: str | None = None) -> list[dict[str, Any]]:
        async with self._pool.acquire() as conn:
            if status is not None:
                rows = await conn.fetch(
                    "SELECT * FROM disputes WHERE status = $1 ORDER BY created_at DESC", status
                )
            else:
                rows = await conn.fetch("SELECT * FROM disputes ORDER BY created_at DESC")
        return [dict(row) for row in rows]

    async def get_detail(self, dispute_id: int) -> DisputeDetail:
        async with self._pool.acquire() as conn:
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

    async def resolve(
        self, dispute_id: int, admin_user_id: UUID, decision: str, reason: str
    ) -> DisputeResolutionResult:
        if decision not in VALID_DECISIONS:
            raise DisputeResolutionError(f"알 수 없는 처리 결정입니다: {decision}")

        detail = await self.get_detail(dispute_id)
        if detail.status != "OPEN":
            raise DisputeResolutionError(
                f"OPEN 상태인 분쟁만 처리할 수 있습니다(현재: {detail.status})."
            )

        new_listing_status = detail.listing_status
        async with self._pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                "UPDATE disputes SET status = 'RESOLVED', resolution_decision = $2, "
                "resolution_reason = $3, resolved_by = $4, resolved_at = now() "
                "WHERE id = $1 RETURNING resolved_at",
                dispute_id,
                decision,
                reason,
                admin_user_id,
            )

            if decision == "DELISTED_AND_REFUND":
                await conn.execute(
                    "UPDATE strategy_listings SET status = 'DELISTED' WHERE id = $1",
                    detail.listing_id,
                )
                new_listing_status = "DELISTED"

            await record_audit_log(
                conn,
                actor_agent=str(admin_user_id),
                action_type="dispute.resolved",
                decision_data={
                    "dispute_id": dispute_id,
                    "decision": decision,
                    "reason": reason,
                    "listing_id": detail.listing_id,
                    "new_listing_status": new_listing_status,
                },
                target_type="dispute",
                target_id=str(dispute_id),
            )

        return DisputeResolutionResult(
            dispute_id=dispute_id,
            listing_status=new_listing_status,
            resolved_at=row["resolved_at"],
        )
