"""FA-15 follow-up — one-time repair for `ledger_balance` rows left behind by a
now-fixed write-path bug that skipped `post_entry`'s `balances.apply()` step.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#9 FA-15,
esc-ci-replay_verify (task-5749), following task-5309/5599/5687's precedent
(302acf56, 83ee0788, c844c5ed, d7a32a0a) of routing every ledger write through
`post_entry`. Those leaves stopped *new* drift; they could not undo drift a
now-removed bug already committed to `ledger_journal_entry`/`ledger_posting_line`
(WORM, cannot be edited) before the fix landed. When that happens,
`ledger_journal_entry` is the source of truth (§4.4) and `ledger_balance` is a
stale derived projection — the correct repair is to recompute the projection
and overwrite the derived row, not to add another journal entry.

Why not post another `MANUAL_ADJUSTMENT` correcting entry instead: that would
extend the very fold `scripts/replay_verify.py` compares against, so the
"replayed" side would move by the same amount the repair just applied to
"actual" -- the two would land back out of sync (`replayed' = replayed +
drift`, `actual' = actual + drift = replayed`, so `replayed' - actual' =
drift != 0` unless drift was already 0). A derived projection that lagged its
source cannot be caught up by adding more source events; only recomputing the
projection converges it back to the source (`src/foundation/ledger/domain/
eventstore_projection.py::project`, the exact fold `replay_verify.py`
already trusts).

Fail-closed: refuses under `ledger_control.write_frozen` (same posture as
`post_entry._assert_not_frozen`) and refuses an account this fold has never
heard of. Idempotent: computing zero drift is a no-op, so re-running this
against an already-clean account never touches the row.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import asyncpg

from src.foundation.ledger.domain import eventstore_projection as ledger_projection
from src.foundation.ledger.ports.balance_repository import BalanceRepository
from src.foundation.ledger.ports.journal_repository import LedgerJournalRepository


class DriftResyncFrozenError(Exception):
    """`ledger_control.write_frozen = true` -- the repair is itself a ledger
    write, so it is blocked the same as any other write once an integrity
    violation has frozen the ledger (fail-closed, §4.4)."""


class UnknownDriftAccountError(Exception):
    """`account_code` is missing from `ledger_account` -- an unknown account
    is rejected instead of silently created (fail-closed, same principle as
    `BalanceRepository.get_for_update`)."""


@dataclass(frozen=True)
class DriftReport:
    account_code: str
    replayed_balance: Decimal
    replayed_last_entry_seq: int
    actual_balance_before: Decimal
    actual_last_entry_seq_before: int
    resynced: bool


async def _assert_not_frozen(conn: asyncpg.Connection) -> None:
    frozen = await conn.fetchval("SELECT write_frozen FROM ledger_control WHERE id = 1 FOR SHARE")
    if frozen:
        raise DriftResyncFrozenError(
            "ledger_control.write_frozen=true -- ledger writes are frozen, refusing drift repair."
        )


async def compute_drift(
    conn: asyncpg.Connection,
    account_code: str,
    *,
    journal: LedgerJournalRepository,
    balances: BalanceRepository,
) -> DriftReport:
    """Computes `account_code`'s fold-vs-actual state only (no write).

    `journal.list_since(conn, 0)` + `ledger_projection.project` is the exact
    code path `scripts/replay_verify.py::_ledger_pairs` uses to build its
    "replayed" side -- reusing it (instead of a second fold implementation)
    guarantees this report can never disagree with replay_verify's own
    verdict."""
    current = await balances.get_for_update(conn, [account_code])
    entries = await journal.list_since(conn, 0)
    projected = ledger_projection.project(entries)
    replayed = projected.get(account_code)
    replayed_balance = Decimal("0") if replayed is None else replayed.balance
    replayed_seq = 0 if replayed is None else replayed.last_entry_seq

    actual = current[account_code]
    resynced = (
        replayed_balance != actual.balance or replayed_seq != actual.last_entry_seq
    )
    return DriftReport(
        account_code=account_code,
        replayed_balance=replayed_balance,
        replayed_last_entry_seq=replayed_seq,
        actual_balance_before=actual.balance,
        actual_last_entry_seq_before=actual.last_entry_seq,
        resynced=resynced,
    )


async def resync_account_balance(
    conn: asyncpg.Connection,
    account_code: str,
    *,
    journal: LedgerJournalRepository,
    balances: BalanceRepository,
) -> DriftReport:
    """Sets `account_code`'s `ledger_balance` to exactly what the journal fold
    computes.

    `report.resynced == False` means it already matched and nothing was
    written (idempotent). `True` means `ledger_balance.balance`/
    `last_entry_seq` were set to the fold's absolute values --
    `BalanceRepository.apply` (delta + always +1) cannot land `last_entry_seq`
    on the fold's true entry count, since the drift itself means the seq
    already skipped an arbitrary amount, so this uses an absolute SET
    instead. The conditional DELETE+INSERT-on-`expected_seq` mechanism is
    otherwise identical to `PostgresBalanceRepository.apply` (not WORM,
    LC-6) -- the only difference is that the assigned values are the fold's
    absolutes, not a delta."""
    await _assert_not_frozen(conn)
    report = await compute_drift(conn, account_code, journal=journal, balances=balances)
    if not report.resynced:
        return report

    row = await conn.fetchrow(
        "WITH target AS MATERIALIZED ("
        " SELECT lb.account_id, lb.held, lb.pending_payout, lb.allow_negative"
        " FROM ledger_balance lb JOIN ledger_account la ON la.account_id = lb.account_id"
        " WHERE la.account_code = $1"
        "), prior AS ("
        " DELETE FROM ledger_balance"
        " WHERE account_id = (SELECT account_id FROM target)"
        " AND last_entry_seq = $4"
        " RETURNING account_id"
        ") "
        "INSERT INTO ledger_balance ("
        " account_id, balance, held, pending_payout, allow_negative,"
        " last_entry_seq, updated_at"
        ") "
        "SELECT t.account_id, $2, t.held, t.pending_payout, t.allow_negative, $3, now() "
        "FROM target t "
        "WHERE EXISTS (SELECT 1 FROM prior) "
        "RETURNING account_id",
        account_code,
        report.replayed_balance,
        report.replayed_last_entry_seq,
        report.actual_last_entry_seq_before,
    )
    if row is None:
        raise UnknownDriftAccountError(account_code)
    return report
