"""18.2 — Dispute ticket query and resolution (DisputeResolutionService).

Spec: functional_spec_v1.20.md#FD-18.2, document #14 §14.5, 8.10,
docs/specs/L4_market_data_positions_ledger_v1.0.md#§4.4 REFUND, §9 LC-14.

Two resolution paths — "NORMAL_RISK_REALIZATION" (listing status unchanged)
and "DELISTED_AND_REFUND" (listing transitions to DELISTED, full price_paid
refunded as buyer credit per ADR-2026-08-29 §1).

This module is the public-facing facade: it owns transaction orchestration
(`resolve`) and repository wiring. Read paths live in
`dispute_resolution/queries.py`; the LC-14 refund/clawback ledger math lives
in `dispute_resolution/refund.py` (see that module's docstring for the
accounting rationale).

Monetary/trust operator decisions are recorded in audit_log per §8.10
(reusing FD-7.2 record_audit_log).
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

import asyncpg
from pydantic import BaseModel

from src.core.logging.audit_log import record_audit_log
from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.ledger.adapters.postgres_balance_repository import PostgresBalanceRepository
from src.foundation.ledger.adapters.postgres_journal_repository import PostgresJournalRepository
from src.services.dispute_resolution.errors import DisputeResolutionError
from src.services.dispute_resolution.queries import (
    DisputeDetail,
    get_detail,
    list_disputes,
)
from src.services.dispute_resolution.refund import refund_with_clawback

__all__ = [
    "DisputeDetail",
    "DisputeResolutionError",
    "DisputeResolutionResult",
    "DisputeResolutionService",
    "VALID_DECISIONS",
]

VALID_DECISIONS = ("NORMAL_RISK_REALIZATION", "DELISTED_AND_REFUND")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class DisputeResolutionResult(BaseModel):
    dispute_id: int
    listing_status: str
    resolved_at: datetime
    refund_amount: Decimal | None = None


class DisputeResolutionService:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool
        self._journal = PostgresJournalRepository(pool)
        self._balances = PostgresBalanceRepository(pool)
        self._audit = PostgresAuditEventRepository(pool)

    @property
    def _ports(self) -> dict[str, Any]:
        return {"journal": self._journal, "balances": self._balances,
                "audit": self._audit, "clock": _utcnow}

    async def list_disputes(self, status: str | None = None) -> list[dict[str, Any]]:
        return await list_disputes(self._pool, status)

    async def get_detail(self, dispute_id: int) -> DisputeDetail:
        return await get_detail(self._pool, dispute_id)

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
        refund_amount: Decimal | None = None
        async with self._pool.acquire() as conn, conn.transaction():
            # RED_TEAM_FINDINGS #05 — Serialize concurrent handling by two admins
            # via status='OPEN' conditional UPDATE in READ COMMITTED
            # (same pattern as confirm_topup()).
            row = await conn.fetchrow(
                "UPDATE disputes SET status = 'RESOLVED', resolution_decision = $2, "
                "resolution_reason = $3, resolved_by = $4, resolved_at = now() "
                "WHERE id = $1 AND status = 'OPEN' RETURNING resolved_at",
                dispute_id, decision, reason, admin_user_id,
            )
            if row is None:
                raise DisputeResolutionError("이미 다른 관리자가 처리했습니다(동시 처리 충돌).")

            if decision == "DELISTED_AND_REFUND":
                await conn.execute(
                    "UPDATE strategy_listings SET status = 'DELISTED' WHERE id = $1",
                    detail.listing_id,
                )
                new_listing_status = "DELISTED"

                # FULL_AUDIT_2026-09-02 §2 — Allow refunded_at conditional UPDATE
                # once only (prevents re-refund on re-dispute; full transaction
                # rollback is the remaining defense).
                purchase = await conn.fetchrow(
                    "UPDATE strategy_purchases SET refunded_at = now() "
                    "WHERE id = $1 AND refunded_at IS NULL "
                    "RETURNING price_paid, platform_commission_rate",
                    detail.purchase_id,
                )
                if purchase is None:
                    raise DisputeResolutionError("이미 환불 처리된 구매 건입니다.")
                price_paid = purchase["price_paid"]
                if price_paid is not None:
                    refund_amount = await refund_with_clawback(
                        conn, purchase_id=detail.purchase_id, buyer_user_id=detail.buyer_user_id,
                        seller_user_id=detail.seller_user_id, price_paid=price_paid,
                        commission_rate=purchase["platform_commission_rate"],
                        admin_id=admin_user_id, ports=self._ports,
                    )

            decision_data = {
                "dispute_id": dispute_id, "decision": decision, "reason": reason,
                "listing_id": detail.listing_id, "new_listing_status": new_listing_status,
                "refund_amount": str(refund_amount) if refund_amount is not None else None,
            }
            await record_audit_log(
                conn, actor_agent=str(admin_user_id), action_type="dispute.resolved",
                decision_data=decision_data, target_type="dispute", target_id=str(dispute_id),
            )

        return DisputeResolutionResult(
            dispute_id=dispute_id,
            listing_status=new_listing_status,
            resolved_at=row["resolved_at"],
            refund_amount=refund_amount,
        )
