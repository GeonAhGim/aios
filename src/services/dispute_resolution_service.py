"""18.2 — Dispute ticket query and resolution (DisputeResolutionService).

Spec: 기능설계문서_v1.20.md#FD-18.2, document #14 §14.5, 8.10,
docs/specs/L4_market_data_positions_ledger_v1.0.md#§4.4 REFUND, §9 LC-14.

Two resolution paths — "NORMAL_RISK_REALIZATION" (listing status unchanged)
and "DELISTED_AND_REFUND" (listing transitions to DELISTED, full price_paid
refunded as buyer credit per ADR-2026-08-29 §1).

LC-14 (task-453) — Audit §1.1 C2 ("refunds do not create money") finally
resolved. `application/refund.py::post_refund` (LC-9 `post_entry` single
path) bundles buyer credit + seller clawback +
`PLATFORM:COMMISSION_REVENUE` reversal into **one journal entry**. Two
correcting entries precede it: (1) `purchase_service._settle` writes the
commission to house `AVAILABLE` immediately after capture, so at refund time
`COMMISSION_REVENUE` is usually 0 — an "un-sweep" restores it; (2) if the
seller has already spent their settlement, move the shortfall from house to
seller `AVAILABLE` first (policy — "house covers immediately instead of
seller `RECEIVABLE` per R3), ensuring `post_refund` always falls only on
R1/R2. On house balance shortage the entire transaction rolls back with
`InsufficientAvailableError` (same principle as red team #41). Legacy
projections that LC-12 bridge cannot express are projected directly using
the same pattern as `purchase_service._project` (side-effect entries after
the ledger has already recorded the truth — no double-counting).

Monetary/trust operator decisions are recorded in audit_log per §8.10
(reusing FD-7.2 record_audit_log).
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import asyncpg
from pydantic import BaseModel

from src.core.logging.audit_log import record_audit_log
from src.data.models.base import Currency
from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.ledger.adapters.postgres_balance_repository import PostgresBalanceRepository
from src.foundation.ledger.adapters.postgres_journal_repository import PostgresJournalRepository
from src.foundation.ledger.application.post_entry import post_entry
from src.foundation.ledger.application.purchase_flow import ensure_account
from src.foundation.ledger.application.refund import post_refund
from src.foundation.ledger.contracts.v1 import LedgerEvent, LedgerEventType, UserSub
from src.foundation.ledger.domain.balance_rules import InsufficientAvailableError
from src.foundation.ledger.domain.chart_of_accounts import (
    PLATFORM_CASH_CLEARING,
    PLATFORM_COMMISSION_REVENUE,
)
from src.foundation.ledger.domain.chart_of_accounts import user_account as ua
from src.foundation.ledger.domain.rounding import split_commission
from src.services.wallet_service import PLATFORM_HOUSE_USER_ID

VALID_DECISIONS = ("NORMAL_RISK_REALIZATION", "DELISTED_AND_REFUND")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class DisputeResolutionError(Exception):
    """FD-18.2 failure — router converts to 400/404."""


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
                    refund_amount = await self._refund_with_clawback(
                        conn, purchase_id=detail.purchase_id, buyer_user_id=detail.buyer_user_id,
                        seller_user_id=detail.seller_user_id, price_paid=price_paid,
                        commission_rate=purchase["platform_commission_rate"],
                        admin_id=admin_user_id,
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

    async def _reconcile_available(self, conn: asyncpg.Connection, user_id: UUID) -> None:
        """Absorb `user_wallets.balance` drift into the ledger (copy of
        `purchase_flow._reconcile_available` — private, cannot reuse). Aligns
        before `_refund_with_clawback` decides based on legacy projections."""
        code = ua(user_id, UserSub.AVAILABLE)
        await ensure_account(conn, code, Currency.KRW)
        projected = await conn.fetchval(
            "SELECT balance FROM user_wallets WHERE user_id = $1", user_id
        ) or Decimal("0")
        drift = projected - await self._balance(conn, code)
        if drift == 0:
            return
        cash = PLATFORM_CASH_CLEARING
        debit, credit = (code, cash) if drift < 0 else (cash, code)
        ref = f"dispute:legacy_sync:{user_id}:{uuid4()}"
        await self._move(conn, debit, credit, abs(drift), ref)

    async def _balance(self, conn: asyncpg.Connection, account_code: str) -> Decimal:
        current = await self._balances.get_for_update(conn, [account_code])
        return current[account_code].balance

    async def _move(
        self, conn: asyncpg.Connection, debit_account: str, credit_account: str,
        amount: Decimal, ref: str, trace_id: UUID | None = None,
    ) -> None:
        event = LedgerEvent(
            event_type=LedgerEventType.MANUAL_ADJUSTMENT, event_ref=ref, tenant_id=None,
            actor_subject_id=None, trace_id=trace_id or uuid4(), amount=amount,
            currency=Currency.KRW, parties={},
            extra={"debit_account": debit_account, "credit_account": credit_account},
        )
        await post_entry(conn, event, **self._ports)

    @staticmethod
    async def _project(  # same pattern as purchase_service.py::_project (see module docstring)
        conn: asyncpg.Connection, user_id: UUID, delta: Decimal, tx_type: str, purchase_id: int
    ) -> None:
        row = await conn.fetchrow(
            "UPDATE user_wallets SET balance = balance + $2, updated_at = now() "
            "WHERE user_id = $1 RETURNING balance",
            user_id, delta,
        )
        if row is None:
            row = await conn.fetchrow(
                "INSERT INTO user_wallets (user_id, balance) VALUES ($1, $2) RETURNING balance",
                user_id, delta,
            )
        await conn.execute(
            "INSERT INTO wallet_transactions (user_id, tx_type, amount, balance_after, "
            "related_purchase_id) VALUES ($1, $2, $3, $4, $5)",
            user_id, tx_type, delta, row["balance"], purchase_id,
        )

    async def _refund_with_clawback(
        self, conn: asyncpg.Connection, *, purchase_id: int, buyer_user_id: UUID,
        seller_user_id: UUID, price_paid: Decimal, commission_rate: Decimal | None, admin_id: UUID,
    ) -> Decimal:
        """Red team #41 / §4.4 REFUND — total balance conservation (see module docstring)."""
        rate = commission_rate if commission_rate is not None else Decimal("0")
        commission_amount, payout_amount = split_commission(price_paid, rate)
        trace_id = uuid4()
        house = ua(PLATFORM_HOUSE_USER_ID, UserSub.AVAILABLE)
        seller_pending = ua(seller_user_id, UserSub.PENDING_PAYOUT)
        seller_avail = ua(seller_user_id, UserSub.AVAILABLE)
        ref = f"refund:purchase:{purchase_id}"

        await ensure_account(conn, seller_pending, Currency.KRW)
        await self._reconcile_available(conn, seller_user_id)
        await self._reconcile_available(conn, PLATFORM_HOUSE_USER_ID)

        seller_take = payout_amount
        shortfall = Decimal("0")
        try:
            if await self._balance(conn, seller_pending) < payout_amount:
                available = await self._balance(conn, seller_avail)
                seller_take = min(available, payout_amount)
                shortfall = payout_amount - seller_take
                if shortfall > 0:
                    await self._move(
                        conn, house, seller_avail, shortfall, f"{ref}:shortfall_cover", trace_id
                    )
            if commission_amount > 0:
                await self._move(
                    conn, house, PLATFORM_COMMISSION_REVENUE, commission_amount,
                    f"{ref}:commission_unsweep", trace_id,
                )
            await post_refund(
                conn, purchase_id=purchase_id, buyer_id=buyer_user_id, seller_id=seller_user_id,
                price=price_paid, commission_rate=rate, admin_id=admin_id, trace_id=trace_id,
                **self._ports,
            )
        except InsufficientAvailableError as exc:
            raise DisputeResolutionError(
                "플랫폼 하우스 지갑 잔액이 부족해 환불을 완료할 수 없습니다 — "
                "하우스 충전 후 다시 처리하세요."
            ) from exc

        await self._project(conn, buyer_user_id, price_paid, "REFUND", purchase_id)
        for uid, delta, tx in (
            (seller_user_id, -seller_take, "REFUND_SELLER_CLAWBACK"),
            (PLATFORM_HOUSE_USER_ID, -commission_amount, "REFUND_COMMISSION_CLAWBACK"),
            (PLATFORM_HOUSE_USER_ID, -shortfall, "REFUND_SHORTFALL_COVER"),
        ):
            if delta != 0:
                await self._project(conn, uid, delta, tx, purchase_id)
        return price_paid
