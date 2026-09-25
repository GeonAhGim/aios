"""18.2 — DELISTED_AND_REFUND clawback ledger math.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§4.4 REFUND, §9 LC-14.

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
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import asyncpg

from src.data.models.base import Currency
from src.foundation.ledger.adapters.postgres_balance_repository import PostgresBalanceRepository
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
from src.services.dispute_resolution.errors import DisputeResolutionError
from src.services.wallet_service import PLATFORM_HOUSE_USER_ID


async def reconcile_available(
    conn: asyncpg.Connection, user_id: UUID, ports: dict[str, Any]
) -> None:
    """Absorb `user_wallets.balance` drift into the ledger (copy of
    `purchase_flow._reconcile_available` — private, cannot reuse). Aligns
    before `refund_with_clawback` decides based on legacy projections."""
    balances: PostgresBalanceRepository = ports["balances"]
    code = ua(user_id, UserSub.AVAILABLE)
    await ensure_account(conn, code, Currency.KRW)
    projected = await conn.fetchval(
        "SELECT balance FROM user_wallets WHERE user_id = $1", user_id
    ) or Decimal("0")
    drift = projected - await _balance(conn, code, balances)
    if drift == 0:
        return
    cash = PLATFORM_CASH_CLEARING
    debit, credit = (code, cash) if drift < 0 else (cash, code)
    ref = f"dispute:legacy_sync:{user_id}:{uuid4()}"
    await _move(conn, debit, credit, abs(drift), ref, ports)


async def _balance(
    conn: asyncpg.Connection, account_code: str, balances: PostgresBalanceRepository
) -> Decimal:
    current = await balances.get_for_update(conn, [account_code])
    return current[account_code].balance


async def _move(
    conn: asyncpg.Connection, debit_account: str, credit_account: str,
    amount: Decimal, ref: str, ports: dict[str, Any], trace_id: UUID | None = None,
) -> None:
    event = LedgerEvent(
        event_type=LedgerEventType.MANUAL_ADJUSTMENT, event_ref=ref, tenant_id=None,
        actor_subject_id=None, trace_id=trace_id or uuid4(), amount=amount,
        currency=Currency.KRW, parties={},
        extra={"debit_account": debit_account, "credit_account": credit_account},
    )
    await post_entry(conn, event, **ports)


async def project(
    conn: asyncpg.Connection, user_id: UUID, delta: Decimal, tx_type: str, purchase_id: int
) -> None:
    """Same pattern as `purchase_service.py::_project` (see module docstring)."""
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


async def refund_with_clawback(
    conn: asyncpg.Connection, *, purchase_id: int, buyer_user_id: UUID,
    seller_user_id: UUID, price_paid: Decimal, commission_rate: Decimal | None, admin_id: UUID,
    ports: dict[str, Any],
) -> Decimal:
    """Red team #41 / §4.4 REFUND — total balance conservation (see module docstring)."""
    balances: PostgresBalanceRepository = ports["balances"]
    rate = commission_rate if commission_rate is not None else Decimal("0")
    commission_amount, payout_amount = split_commission(price_paid, rate)
    trace_id = uuid4()
    house = ua(PLATFORM_HOUSE_USER_ID, UserSub.AVAILABLE)
    seller_pending = ua(seller_user_id, UserSub.PENDING_PAYOUT)
    seller_avail = ua(seller_user_id, UserSub.AVAILABLE)
    ref = f"refund:purchase:{purchase_id}"

    await ensure_account(conn, seller_pending, Currency.KRW)
    await reconcile_available(conn, seller_user_id, ports)
    await reconcile_available(conn, PLATFORM_HOUSE_USER_ID, ports)

    seller_take = payout_amount
    shortfall = Decimal("0")
    try:
        if await _balance(conn, seller_pending, balances) < payout_amount:
            available = await _balance(conn, seller_avail, balances)
            seller_take = min(available, payout_amount)
            shortfall = payout_amount - seller_take
            if shortfall > 0:
                await _move(
                    conn, house, seller_avail, shortfall, f"{ref}:shortfall_cover", ports, trace_id
                )
        if commission_amount > 0:
            await _move(
                conn, house, PLATFORM_COMMISSION_REVENUE, commission_amount,
                f"{ref}:commission_unsweep", ports, trace_id,
            )
        await post_refund(
            conn, purchase_id=purchase_id, buyer_id=buyer_user_id, seller_id=seller_user_id,
            price=price_paid, commission_rate=rate, admin_id=admin_id, trace_id=trace_id,
            **ports,
        )
    except InsufficientAvailableError as exc:
        raise DisputeResolutionError(
            "플랫폼 하우스 지갑 잔액이 부족해 환불을 완료할 수 없습니다 — "
            "하우스 충전 후 다시 처리하세요."
        ) from exc

    await project(conn, buyer_user_id, price_paid, "REFUND", purchase_id)
    for uid, delta, tx in (
        (seller_user_id, -seller_take, "REFUND_SELLER_CLAWBACK"),
        (PLATFORM_HOUSE_USER_ID, -commission_amount, "REFUND_COMMISSION_CLAWBACK"),
        (PLATFORM_HOUSE_USER_ID, -shortfall, "REFUND_SHORTFALL_COVER"),
    ):
        if delta != 0:
            await project(conn, uid, delta, tx, purchase_id)
    return price_paid
