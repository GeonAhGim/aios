"""13.4 — Strategy purchase API (with self-trade prevention).

Spec: 기능설계문서_v1.20.md#FD-13.3, 13번 §13.5

The FD-15.3 risk-grade warning is injected via a DI callback (`check_risk_warning`) —
if a warning exists and there is no explicit acknowledgement, the purchase is blocked.
FD-13.7 commission calculation is recorded together in the same transaction. The
FD-17.1 event is only published when a `publish` callback is provided (so unit tests
can run without an EventBus).

Deviation (ADR-2026-08-29 §1): wallet balance is already verified funds at purchase
time, so `payment_status='CONFIRMED'` is set immediately on success. The listing
lookup takes `FOR UPDATE` to prevent double-debit/settlement from concurrent
purchases. Free listings (price NULL) touch neither the wallet nor the ledger
(zero journal entries, task-424 DoD).

LC-13 (task-424): the 3 `MANUAL_ADJUSTMENT` entries for buyer debit, seller
settlement, and commission accrual were replaced with a single
`purchase_flow.place_hold` (HOLD_PLACED) + `capture_hold` (HOLD_CAPTURED) pair
(see that module's docstring for the concurrent-purchase defense). Immediate
settlement of sale proceeds and the commission's reflection into the
`PLATFORM_HOUSE_USER_ID` wallet (the existing test_dispute_resolution_service.py
refund callback consumes that balance and must keep passing) are preserved via
`_settle` right after the capture. The public signature and response schema are
unchanged.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import asyncpg
from pydantic import BaseModel

from src.data.models.base import Currency
from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.ledger.adapters.postgres_balance_repository import PostgresBalanceRepository
from src.foundation.ledger.adapters.postgres_hold_repository import PostgresHoldRepository
from src.foundation.ledger.adapters.postgres_journal_repository import PostgresJournalRepository
from src.foundation.ledger.application.post_entry import post_entry
from src.foundation.ledger.application.purchase_flow import (
    HoldConflictError,
    capture_hold,
    ensure_account,
    place_hold,
)
from src.foundation.ledger.contracts.v1 import LedgerEvent, LedgerEventType, UserSub
from src.foundation.ledger.domain.balance_rules import InsufficientAvailableError
from src.foundation.ledger.domain.chart_of_accounts import PLATFORM_COMMISSION_REVENUE
from src.foundation.ledger.domain.chart_of_accounts import user_account as ua
from src.services.commission import DEFAULT_COMMISSION_RATE, calculate_commission
from src.services.wallet_service import PLATFORM_HOUSE_USER_ID

_HOLD_PURPOSE = "MARKETPLACE_PURCHASE"
_HOLD_TTL_MINUTES = 15


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


CheckRiskWarningFn = Callable[[UUID, str, str], Awaitable[str | None]]
PublishFn = Callable[[str, dict[str, Any]], Awaitable[None]]


async def _no_risk_warning(buyer_user_id: UUID, strategy_id: str, strategy_version: str) -> None:
    return None


class PurchaseError(Exception):
    """FD-13.3 failure — the router converts this to 400/403/404/409."""


class InsufficientWalletBalanceError(PurchaseError):
    """Insufficient wallet balance — the router converts this to 402."""


class PurchaseResult(BaseModel):
    purchase_id: int
    status: str
    risk_warning: str | None = None
    platform_commission_amount: Decimal | None = None
    seller_payout_amount: Decimal | None = None


class PurchaseService:
    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        check_risk_warning: CheckRiskWarningFn = _no_risk_warning,
        commission_rate: Decimal = DEFAULT_COMMISSION_RATE,
        publish: PublishFn | None = None,
    ) -> None:
        self._pool = pool
        self._check_risk_warning = check_risk_warning
        self._commission_rate = commission_rate
        self._publish = publish
        self._journal = PostgresJournalRepository(pool)
        self._balances = PostgresBalanceRepository(pool)
        self._audit = PostgresAuditEventRepository(pool)
        self._holds = PostgresHoldRepository(pool)

    @property
    def _ports(self) -> dict[str, Any]:
        return {
            "journal": self._journal,
            "balances": self._balances,
            "audit": self._audit,
            "clock": _utcnow,
        }

    async def purchase(
        self,
        buyer_user_id: UUID,
        listing_id: int,
        *,
        risk_warning_acknowledged: bool = False,
    ) -> PurchaseResult:
        warning: str | None = None
        async with self._pool.acquire() as conn, conn.transaction():
            listing = await conn.fetchrow(
                "SELECT * FROM strategy_listings WHERE id = $1 FOR UPDATE", listing_id
            )
            if listing is None:
                raise PurchaseError("존재하지 않는 리스팅입니다.")
            if listing["status"] != "LISTED":
                raise PurchaseError(f"구매할 수 없는 리스팅 상태입니다(현재: {listing['status']}).")
            if listing["seller_user_id"] == buyer_user_id:
                raise PurchaseError("본인이 판매 중인 전략은 구매할 수 없습니다.")

            # Reflects the full audit (docs/FULL_AUDIT_2026-09-02.md §2) — the same
            # buyer re-requesting the same listing with a different Idempotency-Key
            # used to debit/settle twice. The FOR UPDATE above serializes concurrent
            # purchases of the same listing, so this lookup is race-safe. The DB
            # UNIQUE(listing_id, buyer_user_id) is the last line of defense layered
            # on top (handled below as UniqueViolationError).
            already_purchased = await conn.fetchval(
                "SELECT id FROM strategy_purchases WHERE listing_id = $1 AND buyer_user_id = $2",
                listing_id,
                buyer_user_id,
            )
            if already_purchased is not None:
                raise PurchaseError("이미 구매한 리스팅입니다.")

            warning = await self._check_risk_warning(
                buyer_user_id, listing["strategy_id"], listing["strategy_version"]
            )
            if warning is not None and not risk_warning_acknowledged:
                raise PurchaseError(warning)

            price_paid = listing["price"]
            commission_amount, seller_payout_amount = calculate_commission(
                price_paid, self._commission_rate
            )
            commission_rate = self._commission_rate if price_paid is not None else None

            try:
                row = await conn.fetchrow(
                    "INSERT INTO strategy_purchases "
                    "(listing_id, buyer_user_id, price_paid, platform_commission_rate, "
                    "platform_commission_amount, seller_payout_amount, payment_status, "
                    "confirmed_at) "
                    "VALUES ($1, $2, $3, $4, $5, $6, 'CONFIRMED', now()) "
                    "RETURNING id, payment_status",
                    listing_id,
                    buyer_user_id,
                    price_paid,
                    commission_rate,
                    commission_amount,
                    seller_payout_amount,
                )
            except asyncpg.UniqueViolationError as exc:
                raise PurchaseError("이미 구매한 리스팅입니다.") from exc

            if price_paid is not None:
                assert seller_payout_amount is not None  # calculate_commission invariant
                purchase_id = row["id"]
                seller_user_id = listing["seller_user_id"]
                reference = f"purchase:{purchase_id}"
                trace_id = uuid4()

                try:
                    hold = await place_hold(
                        conn,
                        buyer_id=buyer_user_id,
                        amount=price_paid,
                        purpose=_HOLD_PURPOSE,
                        reference=reference,
                        actor_subject_id=buyer_user_id,
                        trace_id=trace_id,
                        expires_at=_utcnow() + timedelta(minutes=_HOLD_TTL_MINUTES),
                        holds=self._holds,
                        **self._ports,
                    )
                except HoldConflictError as exc:
                    raise PurchaseError("이미 처리 중인 구매 요청입니다.") from exc
                except InsufficientAvailableError as exc:
                    raise InsufficientWalletBalanceError(str(exc)) from exc
                await self._project(conn, buyer_user_id, -price_paid, "PURCHASE_DEBIT", purchase_id)

                capture = await capture_hold(
                    conn,
                    hold,
                    seller_id=seller_user_id,
                    commission_rate=self._commission_rate,
                    actor_subject_id=buyer_user_id,
                    trace_id=trace_id,
                    now=_utcnow(),
                    holds=self._holds,
                    **self._ports,
                )
                assert (capture.payout_amount, capture.commission_amount) == (
                    seller_payout_amount,
                    commission_amount,
                )  # split_commission round-trip invariant

                # Preserve immediate settlement of sale proceeds (same rationale as the
                # backfill reconstruction, see the class docstring) — moves the
                # PENDING_PAYOUT that HOLD_CAPTURED established straight to AVAILABLE.
                await self._settle(
                    conn,
                    LedgerEventType.PAYOUT_RELEASE,
                    f"{reference}:release",
                    amount=seller_payout_amount,
                    parties={"seller": seller_user_id},
                    project_user_id=seller_user_id,
                    tx_type="SALE_CREDIT",
                    purchase_id=purchase_id,
                )
                if commission_amount:
                    extra: dict[str, Decimal | str] = {
                        "debit_account": PLATFORM_COMMISSION_REVENUE,
                        "credit_account": ua(PLATFORM_HOUSE_USER_ID, UserSub.AVAILABLE),
                    }
                    await self._settle(
                        conn,
                        LedgerEventType.MANUAL_ADJUSTMENT,
                        f"{reference}:commission_sweep",
                        amount=commission_amount,
                        extra=extra,
                        project_user_id=PLATFORM_HOUSE_USER_ID,
                        tx_type="COMMISSION_CREDIT",
                        purchase_id=purchase_id,
                    )

        if self._publish is not None:
            await self._publish(
                "marketplace.purchase.requested",
                {
                    "event_type": "marketplace.purchase.requested",
                    "user_id": str(buyer_user_id),
                    "purchase_id": row["id"],
                    "listing_id": listing_id,
                },
            )
            if warning is not None and risk_warning_acknowledged:
                await self._publish(
                    "risk_profile.match.warned",
                    {
                        "event_type": "risk_profile.match.warned",
                        "user_id": str(buyer_user_id),
                        "reason": warning,
                    },
                )

        return PurchaseResult(
            purchase_id=row["id"],
            status=row["payment_status"],
            risk_warning=warning if risk_warning_acknowledged else None,
            platform_commission_amount=commission_amount,
            seller_payout_amount=seller_payout_amount,
        )

    async def _settle(
        self,
        conn: asyncpg.Connection,
        event_type: LedgerEventType,
        event_ref: str,
        *,
        amount: Decimal,
        project_user_id: UUID,
        tx_type: str,
        purchase_id: int,
        parties: dict[str, UUID] | None = None,
        extra: dict[str, Decimal | str] | None = None,
    ) -> None:
        """Handles the post-capture settlement journal entry (PAYOUT_RELEASE/
        MANUAL_ADJUSTMENT) and the legacy projection together in one call. The
        related account may be used for the first time under §4.4, so its
        existence is ensured first."""
        if extra:
            await ensure_account(conn, str(extra["credit_account"]), Currency.KRW)
        for user_id in (parties or {}).values():
            await ensure_account(conn, ua(user_id, UserSub.AVAILABLE), Currency.KRW)
        event = LedgerEvent(
            event_type=event_type,
            event_ref=event_ref,
            tenant_id=None,
            actor_subject_id=None,
            trace_id=uuid4(),
            amount=amount,
            currency=Currency.KRW,
            parties=parties or {},
            extra=extra or {},
        )
        await post_entry(conn, event, **self._ports)
        await self._project(conn, project_user_id, amount, tx_type, purchase_id)

    @staticmethod
    async def _project(
        conn: asyncpg.Connection, user_id: UUID, delta: Decimal, tx_type: str, purchase_id: int
    ) -> None:
        """Reproduces the `user_wallets`/`wallet_transactions` (legacy projection)
        update that would have happened automatically had callers kept using
        `wallet_service.debit/credit` (→ bridge)."""
        row = await conn.fetchrow(
            "UPDATE user_wallets SET balance = balance + $2, updated_at = now() "
            "WHERE user_id = $1 RETURNING balance",
            user_id,
            delta,
        )
        if row is None:
            row = await conn.fetchrow(
                "INSERT INTO user_wallets (user_id, balance) VALUES ($1, $2) RETURNING balance",
                user_id,
                delta,
            )
        await conn.execute(
            "INSERT INTO wallet_transactions "
            "(user_id, tx_type, amount, balance_after, related_purchase_id) "
            "VALUES ($1, $2, $3, $4, $5)",
            user_id,
            tx_type,
            delta,
            row["balance"],
            purchase_id,
        )
