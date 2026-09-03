"""18.2 — 분쟁 티켓 조회·처리 (DisputeResolutionService).

Spec: 기능설계문서_v1.20.md#FD-18.2, 14번 문서 §14.5, 8.10,
docs/specs/L4_market_data_positions_ledger_v1.0.md#§4.4 REFUND, §9 LC-14.

처리 결정은 두 갈래 — "정상 리스크 실현"(리스팅 상태 불변)과 "DELISTED +
환불"(리스팅 DELISTED 전환, price_paid 전액 buyer 환불 크레딧, ADR-2026-08-29 §1).

LC-14(task-453) — 감사 §1.1 C2("환불이 돈을 생성") 최종 해소.
`application/refund.py::post_refund`(LC-9 `post_entry` 단일 경로) 하나가
buyer 적립 + seller 환수 + `PLATFORM:COMMISSION_REVENUE` 환수를 **한
분개**로 묶는다. 그 앞에 두 보정 분개가 필요하다: (1) `purchase_service.
_settle`가 캡처 직후 커미션을 house `AVAILABLE`로 쓸어가 환불 시점엔
`COMMISSION_REVENUE`가 대개 0이라 되돌리는 "un-sweep", (2) seller가
정산금을 이미 다 썼으면 house에서 차액만큼 seller `AVAILABLE`로 먼저
옮겨(정책 — "부족분은 house가 즉시 메운다", R3의 seller `RECEIVABLE`
대신) `post_refund`가 항상 R1/R2로만 떨어지게 한다. house 잔액 부족 시
`InsufficientAvailableError`로 트랜잭션 전체가 롤백된다(레드팀 #41과
동일 원칙). 레거시 투영은 LC-12 브리지로 표현 못 할 다계정 흐름이라
`purchase_service._project`와 동일 패턴으로 직접 투영한다(원장이 이미
진실을 기록한 **뒤**의 부수 기록일 뿐이라 이중 반영이 아니다).

금전/신뢰 관련 운영자 판단이라 8.10 원칙에 따라 audit_log에 기록한다
(FD-7.2 record_audit_log 재사용).
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
            # RED_TEAM_FINDINGS #05 — READ COMMITTED에서 두 관리자의 동시 처리를
            # status='OPEN' 조건부 UPDATE로 직렬화(confirm_topup()과 동일 패턴).
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

                # FULL_AUDIT_2026-09-02 §2 — 재분쟁으로 재환불되던 것을 refunded_at
                # 조건부 UPDATE로 한 번만 허용(트랜잭션 전체 롤백이 나머지 방어).
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
        """`user_wallets.balance` drift를 원장에 흡수(`purchase_flow.
        _reconcile_available` 사본 — private이라 재사용 불가). 아래
        `_refund_with_clawback`이 legacy 투영 기준으로 판정하기 전에 맞춘다."""
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
    async def _project(  # purchase_service.py::_project와 동일 패턴(모듈 docstring 참고)
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
        """레드팀 #41 / §4.4 REFUND — 총잔액 보존(모듈 docstring 참고)."""
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
