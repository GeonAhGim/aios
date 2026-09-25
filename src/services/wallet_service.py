"""FD-13.11 (new) — Marketplace currency mapped to platform-internal credit (points) wallet.

Spec: 14_marketplace_detailed_v1.1.md §14.1 (pricing currency=KRW, absence of
auto PG intentionally designed, proceed after Chapter 19 legal review) — preserves
those principles and adds an internal wallet layer on top. Full background in
ADR-2026-08-29-wallet-marketplace-dual-seller-strategy-authoring.md §1 — summary:
P2P trades between users would trigger electronic money business registration
if the platform intermediates actual bank transfers per transaction, so "top-up
(KRW deposit → manual admin confirmation, same pattern as the former
payment_confirmation_service.py)" and "purchase (wallet balance debit, immediate
settlement)" are separated. 1 credit = 1 KRW fixed — no separate exchange-rate
or issuance logic (same KRW-single-currency approach as §11.1 Money type rules,
only the display unit is called "credit").

Deviation: payment_confirmation_service.py (FD-18.5a/18.5b, per-purchase payment
confirmation) is fully replaced and removed by this leaf — wallet balance is
already validated at purchase time, so the intermediate PENDING_PAYMENT state
requiring post-hoc admin confirmation no longer occurs. The point needing admin
confirmation moves to "top-up request".

Settlement recipient for seller_type='PLATFORM' listings (ADR §2, treated under
the same commission structure) is this house account — the platform commissions
itself, so effectively the full sale amount accumulates in this wallet
(accounting-natural, no special branch needed in purchase_service.py).

LC-12 (§5.4 phase 3) — `debit`/`credit` delegate to `legacy_wallet_bridge`,
`confirm_topup` delegates to `application/topup.post_topup` (details in those
modules' docstrings). Public signatures and `InsufficientBalanceError` are
invariant; the source of truth is now `ledger_balance`, with
`user_wallets`/`wallet_transactions` as projections.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

import asyncpg
from pydantic import BaseModel

from src.core.logging.audit_log import record_audit_log
from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.ledger.adapters.legacy_wallet_bridge import (
    BridgeInsufficientBalanceError,
    bridge_credit,
    bridge_debit,
)
from src.foundation.ledger.adapters.postgres_balance_repository import PostgresBalanceRepository
from src.foundation.ledger.adapters.postgres_journal_repository import PostgresJournalRepository
from src.foundation.ledger.application.topup import post_topup

DEFAULT_PAGE_SIZE = 20

PLATFORM_HOUSE_USER_ID = UUID("00000000-0000-0000-0000-000000000001")
"""Reserved system account used for marketplace commission receipt and PLATFORM
listing seller. The migration db/migrations/versions/e7f8a9b0c1d2_wallet_ledger.py
seeds the users/user_wallets row with this same UUID."""

_WALLET_TX_TYPES = frozenset(
    {
        "TOPUP",
        "PURCHASE_DEBIT",
        "SALE_CREDIT",
        "COMMISSION_CREDIT",
        "REFUND",
        # Red team #41 — seller payout and platform commission clawback on refund.
        # Previously only credited the buyer with price_paid without clawing back
        # from seller/house, causing system total balance to increase by price_paid
        # per refund (money created from nothing).
        "REFUND_SELLER_CLAWBACK",
        "REFUND_COMMISSION_CLAWBACK",
        "REFUND_SHORTFALL_COVER",
    }
)


class InsufficientBalanceError(Exception):
    """Insufficient balance — caller (purchase_service, etc.) converts to HTTP status."""


class WalletTopupError(Exception):
    """Top-up request processing failure — router converts to 400/404."""


class WalletTopupNotFoundError(WalletTopupError):
    """QA task-1163 — non-existent top-up request. RESOURCE_NOT_FOUND (404)."""


class WalletTopupInvalidTransitionError(WalletTopupError):
    """QA task-1163 — reconfirmation attempt on a request already transitioned to
    CONFIRMED (or in-flight). STATE_INVALID_TRANSITION (409)."""


class WalletBalance(BaseModel):
    user_id: UUID
    balance: Decimal


class WalletTopupRequest(BaseModel):
    id: int
    user_id: UUID
    requested_amount: Decimal
    status: str
    requested_at: datetime
    confirmed_at: datetime | None
    confirmed_by: UUID | None = None


class WalletTopupPage(BaseModel):
    items: list[WalletTopupRequest]
    total: int
    page: int
    page_size: int


class WalletTopupConfirmResult(BaseModel):
    id: int
    status: str
    balance_after: Decimal | None
    confirmed_at: datetime | None


async def debit(
    conn: asyncpg.Connection,
    user_id: UUID,
    amount: Decimal,
    tx_type: str,
    *,
    related_purchase_id: int | None = None,
) -> Decimal:
    """Call only inside the caller's `conn.transaction()`. Balance-sufficient
    validation is handled by `post_entry` (LC-9) via `FOR UPDATE` +
    `allow_negative=False`."""
    assert tx_type in _WALLET_TX_TYPES, f"알 수 없는 거래 유형: {tx_type}"
    try:
        return await bridge_debit(
            conn, user_id, amount, tx_type, related_purchase_id=related_purchase_id
        )
    except BridgeInsufficientBalanceError as exc:
        raise InsufficientBalanceError("지갑 잔액이 부족합니다.") from exc


async def credit(
    conn: asyncpg.Connection,
    user_id: UUID,
    amount: Decimal,
    tx_type: str,
    *,
    related_purchase_id: int | None = None,
) -> Decimal:
    """Creates the projection wallet entry for users who do not yet have a wallet
    (first top-up or refund after signup)."""
    assert tx_type in _WALLET_TX_TYPES, f"알 수 없는 거래 유형: {tx_type}"
    return await bridge_credit(
        conn, user_id, amount, tx_type, related_purchase_id=related_purchase_id
    )


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class WalletService:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool
        self._journal = PostgresJournalRepository(pool)
        self._balances = PostgresBalanceRepository(pool)
        self._audit = PostgresAuditEventRepository(pool)

    async def get_balance(self, user_id: UUID) -> WalletBalance:
        async with self._pool.acquire() as conn:
            balance = await conn.fetchval(
                "SELECT balance FROM user_wallets WHERE user_id = $1", user_id
            )
        return WalletBalance(
            user_id=user_id, balance=balance if balance is not None else Decimal("0")
        )

    async def request_topup(self, user_id: UUID, amount: Decimal) -> WalletTopupRequest:
        if amount <= 0:
            raise WalletTopupError("충전 금액은 0보다 커야 합니다.")
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO wallet_topup_requests (user_id, requested_amount) "
                "VALUES ($1, $2) RETURNING *",
                user_id, amount,
            )
        return WalletTopupRequest(**dict(row))

    async def list_pending_topups(
        self, *, page: int = 1, page_size: int = DEFAULT_PAGE_SIZE
    ) -> WalletTopupPage:
        async with self._pool.acquire() as conn:
            total = await conn.fetchval(
                "SELECT COUNT(*) FROM wallet_topup_requests WHERE status = 'PENDING'"
            )
            rows = await conn.fetch(
                "SELECT * FROM wallet_topup_requests WHERE status = 'PENDING' "
                "ORDER BY requested_at ASC LIMIT $1 OFFSET $2",
                page_size, (page - 1) * page_size,
            )
        return WalletTopupPage(
            items=[WalletTopupRequest(**dict(row)) for row in rows],
            total=total,
            page=page,
            page_size=page_size,
        )

    async def confirm_topup(
        self,
        topup_id: int,
        admin_user_id: UUID,
        *,
        idempotency_key: str,  # noqa: ARG002 — DB state itself is the idempotency guard (see below)
    ) -> WalletTopupConfirmResult:
        async with self._pool.acquire() as conn, conn.transaction():
            current = await conn.fetchrow(
                "SELECT user_id, requested_amount, status, confirmed_at "
                "FROM wallet_topup_requests WHERE id = $1",
                topup_id,
            )
            if current is None:
                raise WalletTopupNotFoundError("존재하지 않는 충전 요청입니다.")

            if current["status"] == "CONFIRMED":
                return WalletTopupConfirmResult(
                    id=topup_id, status="CONFIRMED", balance_after=None,
                    confirmed_at=current["confirmed_at"],
                )

            updated = await conn.fetchrow(
                "UPDATE wallet_topup_requests SET status = 'CONFIRMED', confirmed_at = now(), "
                "confirmed_by = $2 WHERE id = $1 AND status = 'PENDING' RETURNING confirmed_at",
                topup_id, admin_user_id,
            )
            if updated is None:
                raise WalletTopupInvalidTransitionError(
                    "이미 다른 관리자가 처리했습니다(동시 처리 충돌)."
                )

            balance_after = await post_topup(
                conn, topup_id, current["user_id"], current["requested_amount"], admin_user_id,
                journal=self._journal, balances=self._balances, audit=self._audit, clock=_utcnow,
            )

            await record_audit_log(
                conn, actor_agent=str(admin_user_id), action_type="wallet.topup.confirmed",
                decision_data={
                    "topup_id": topup_id, "user_id": str(current["user_id"]),
                    "amount": str(current["requested_amount"]),
                },
                target_type="wallet_topup_request", target_id=str(topup_id),
            )

        return WalletTopupConfirmResult(
            id=topup_id, status="CONFIRMED", balance_after=balance_after,
            confirmed_at=updated["confirmed_at"],
        )
