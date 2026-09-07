"""LA-25b — ledger posting of cash dividends.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§4.4, §9 LA-25b,
ADR-2026-09-06-G §9 "cash dividends never reach the ledger (only reference
data is recorded)".

`record_corporate_action` (LA-14) records CASH_DIVIDEND only in
`md_corporate_action` (reference data) and stops there — the dividend cash
is absent from the ledger (`ledger_journal_entry`). This function closes
that gap: given a `CorporateAction` already recorded as reference
(action_type="CASH_DIVIDEND") and the finalized payout `amount` for one
holder, it posts a single journal entry via `post_entry` (LC-9). Computing
`amount` from held quantity x per-share dividend (position lookup, I/O) is
out of scope for this leaf — the DoD only requires "CASH_DIVIDEND with
amount>0 is posted to the holder's AVAILABLE"; who held how much is the
responsibility of a separate layer (the position read model).

Adding a new `LedgerEventType` enum value (e.g. CASH_DIVIDEND) would require
a new migration that ALTERs the `ledger_journal_entry.event_type` CHECK
constraint (the LC-6 migration) — this leaf does not create a migration
without PM approval (parent revision) (headless worker protocol, same
judgment as the `chargeback.py` module docstring). Instead it reuses the
already-existing `MANUAL_ADJUSTMENT` (LC-4, the "explicit row" path) to post
`PLATFORM:CASH_CLEARING` -> `USER:{holder}:AVAILABLE` — the
`ledger_journal_entry.event_type` column value stays MANUAL_ADJUSTMENT, but
`event_ref` is pinned to
`"corp_action_cash:{instrument_id}:{ex_date}:{holder_id}"` so the
`idempotency_key` (`{event_type}:{event_ref}`) becomes unique exactly on
(instrument, ex_date, holder), and the audit payload (`event_ref`) lets the
cause be traced.

If `amount <= 0`, nothing is posted and `None` is returned — the DoD only
requires posting "CASH_DIVIDEND with amount>0"; there is no basis for
turning a non-positive amount into a journal entry (`PostingLine.amount`
must also always be `> 0`, §3.3).
"""
from __future__ import annotations

from decimal import Decimal
from uuid import UUID

import asyncpg

from src.data.models.base import Currency
from src.foundation.ledger.application.post_entry import AuditAppender, Clock, post_entry
from src.foundation.ledger.application.purchase_flow import ensure_account
from src.foundation.ledger.contracts.v1 import (
    JournalEntryView,
    LedgerEvent,
    LedgerEventType,
    UserSub,
)
from src.foundation.ledger.domain.chart_of_accounts import PLATFORM_CASH_CLEARING
from src.foundation.ledger.domain.chart_of_accounts import user_account as ua
from src.foundation.ledger.ports.balance_repository import BalanceRepository
from src.foundation.ledger.ports.journal_repository import LedgerJournalRepository
from src.foundation.market_data.contracts.v1 import CorporateAction

__all__ = ["UnsupportedCorporateActionError", "post_corporate_action_cash"]


class UnsupportedCorporateActionError(ValueError):
    """`action.action_type` is not "CASH_DIVIDEND" — this function is cash-
    dividend only (splits/mergers/reverse-splits are another domain's
    responsibility, handled respectively by §9 BT-20's backtest adjustment
    and LA-8's `domain/corporate_actions/adjustment.py`)."""

    def __init__(self, action_type: str) -> None:
        super().__init__(f"CASH_DIVIDEND 전용 함수: action_type={action_type!r}")
        self.action_type = action_type


def _event_ref(action: CorporateAction, holder_id: UUID) -> str:
    """A reference unique on the `(instrument, ex_date, holder)` triple.
    Combined with `MANUAL_ADJUSTMENT`, the `idempotency_key`
    (`{event_type}:{event_ref}`) uses this value to produce idempotency on
    exactly that triple."""
    return f"corp_action_cash:{action.instrument_id}:{action.ex_date.isoformat()}:{holder_id}"


async def post_corporate_action_cash(
    conn: asyncpg.Connection,
    action: CorporateAction,
    *,
    holder_id: UUID,
    amount: Decimal,
    admin_id: UUID | None,
    trace_id: UUID,
    journal: LedgerJournalRepository,
    balances: BalanceRepository,
    audit: AuditAppender,
    clock: Clock,
    currency: Currency = Currency.KRW,
) -> JournalEntryView | None:
    """Posts holder `holder_id`'s finalized payout `amount` for the
    CASH_DIVIDEND reference data `action` to that holder's `AVAILABLE`
    account. If `amount <= 0`, posts nothing and returns `None`. A repeat
    call (same instrument/ex_date/holder, same amount) does not create a new
    entry, via `post_entry`'s (LC-9) REPLAY — a repeat call with a different
    amount raises `IdempotencyDigestMismatchError` (409, non-retriable)."""
    if action.action_type != "CASH_DIVIDEND":
        raise UnsupportedCorporateActionError(action.action_type)
    if amount <= 0:
        return None

    holder_available = ua(holder_id, UserSub.AVAILABLE)
    await ensure_account(conn, holder_available, currency)

    event = LedgerEvent(
        event_type=LedgerEventType.MANUAL_ADJUSTMENT,
        event_ref=_event_ref(action, holder_id),
        tenant_id=None,
        actor_subject_id=admin_id,
        trace_id=trace_id,
        amount=amount,
        currency=currency,
        parties={"holder": holder_id},
        extra={"debit_account": PLATFORM_CASH_CLEARING, "credit_account": holder_available},
    )
    return await post_entry(
        conn, event, journal=journal, balances=balances, audit=audit, clock=clock
    )
