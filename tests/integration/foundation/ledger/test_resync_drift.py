"""FA-15 follow-up -- `resync_drift.py` integration tests (task-5749).

D3 (this module sits on the ledger/`L4`/`FA` money axis): the adversarial
test below is cross-checked against `docs/design/INVARIANTS.md` I-10
("implemented != working -- safety/policy components need a wiring-proof
test, static or adversarial integration") by literally driving
`scripts/replay_verify.py`'s own `verify()` FAIL -> repair -> OK, the same
subprocess-level proof `test_replay_verify.py::
test_replay_detects_ledger_balance_tampered_outside_the_event_trail` uses
for the checker itself.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from scripts import replay_verify
from src.data.models.base import Currency
from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.ledger.adapters.postgres_balance_repository import (
    PostgresBalanceRepository,
    UnknownAccountError,
)
from src.foundation.ledger.adapters.postgres_journal_repository import PostgresJournalRepository
from src.foundation.ledger.application.post_entry import post_entry
from src.foundation.ledger.application.resync_drift import (
    DriftResyncFrozenError,
    compute_drift,
    resync_account_balance,
)
from src.foundation.ledger.contracts.v1 import LedgerEvent, LedgerEventType, UserSub
from src.foundation.ledger.domain.chart_of_accounts import user_account


def _clock() -> datetime:
    return datetime.now(timezone.utc)


async def _seed_ledger_entry(pool, *, amount: Decimal = Decimal("10.00")) -> str:
    """One MANUAL_ADJUSTMENT between two fresh `USER:*` accounts, all via
    `post_entry` -- never the real `PLATFORM:CASH_CLEARING`/
    `COMMISSION_REVENUE` accounts the live CI mismatch actually touches, so
    this test cannot itself add drift to the shared TEST_DATABASE_URL the
    way esc-2115 warns against."""
    journal = PostgresJournalRepository(pool)
    balances = PostgresBalanceRepository(pool)
    audit = PostgresAuditEventRepository(pool)
    debit_code = user_account(uuid4(), UserSub.RECEIVABLE)
    credit_code = user_account(uuid4(), UserSub.AVAILABLE)
    async with pool.acquire() as conn:
        for code, allow_negative in ((debit_code, True), (credit_code, False)):
            account_id = await conn.fetchval(
                "INSERT INTO ledger_account (account_code, account_type, currency, allow_negative) "
                "VALUES ($1, $2, $3, $4) RETURNING account_id",
                code,
                ("ASSET" if allow_negative else "LIABILITY"),
                Currency.KRW.value,
                allow_negative,
            )
            await conn.execute(
                "INSERT INTO ledger_balance (account_id, allow_negative, last_entry_seq) "
                "VALUES ($1, $2, 0)",
                account_id,
                allow_negative,
            )

    event = LedgerEvent(
        event_type=LedgerEventType.MANUAL_ADJUSTMENT,
        event_ref=f"adj:{uuid4().hex}",
        tenant_id=None,
        actor_subject_id=None,
        trace_id=uuid4(),
        amount=amount,
        currency=Currency.KRW,
        parties={},
        extra={"debit_account": debit_code, "credit_account": credit_code},
    )
    async with pool.acquire() as conn, conn.transaction():
        await post_entry(conn, event, journal=journal, balances=balances, audit=audit, clock=_clock)
    return debit_code


async def _bump_balance_outside_event_trail(pool, account_code: str, delta: int) -> None:
    """Reproduces the exact bug class task-5749 root-causes: a real, permanent
    `ledger_balance` row mutated without a matching journal entry (mirrors
    `test_replay_verify.py::test_replay_detects_ledger_balance_tampered_
    outside_the_event_trail`'s `_bump_balance`). FA-10's no-UPDATE trigger
    forces DELETE+INSERT even for this tamper.
    # audit-allow: ledger_balance_raw_seed -- adversarial tamper of an
    # already-`post_entry`-seeded row, not an initial-balance seed.
    """
    async with pool.acquire() as conn:
        await conn.execute(
            "WITH removed AS ("
            " DELETE FROM ledger_balance WHERE account_id = "
            " (SELECT account_id FROM ledger_account WHERE account_code = $2)"
            " RETURNING account_id, balance, held, pending_payout, allow_negative,"
            " last_entry_seq, updated_at"
            ") "
            "INSERT INTO ledger_balance ("
            " account_id, balance, held, pending_payout, allow_negative,"
            " last_entry_seq, updated_at"
            ") "
            "SELECT account_id, balance + $1, held, pending_payout, allow_negative,"
            " last_entry_seq, updated_at FROM removed",
            delta,
            account_code,
        )


async def _row(pool, account_code: str) -> tuple[Decimal, int]:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT lb.balance, lb.last_entry_seq FROM ledger_balance lb "
            "JOIN ledger_account la ON la.account_id = lb.account_id "
            "WHERE la.account_code = $1",
            account_code,
        )
    assert row is not None
    return row["balance"], row["last_entry_seq"]


async def test_compute_drift_reports_no_drift_for_a_clean_account(pool):
    journal = PostgresJournalRepository(pool)
    balances = PostgresBalanceRepository(pool)
    debit_code = await _seed_ledger_entry(pool)

    async with pool.acquire() as conn, conn.transaction():
        report = await compute_drift(conn, debit_code, journal=journal, balances=balances)

    assert report.resynced is False
    assert report.replayed_balance == report.actual_balance_before


async def test_resync_writes_nothing_when_already_synced(pool):
    journal = PostgresJournalRepository(pool)
    balances = PostgresBalanceRepository(pool)
    debit_code = await _seed_ledger_entry(pool)
    before = await _row(pool, debit_code)

    async with pool.acquire() as conn, conn.transaction():
        report = await resync_account_balance(conn, debit_code, journal=journal, balances=balances)

    assert report.resynced is False
    assert await _row(pool, debit_code) == before


async def test_resync_detects_and_repairs_drift_matching_replay_verify(pool):
    """The D3 wiring-proof (INVARIANTS.md I-10): tamper -> `replay_verify`
    FAIL (gate-red repro) -> `resync_account_balance` -> `replay_verify` OK
    (green evidence), all against the exact fold `scripts/replay_verify.py`
    itself trusts -- see `resync_drift.py`'s module docstring for why a new
    correcting journal entry cannot achieve this."""
    journal = PostgresJournalRepository(pool)
    balances = PostgresBalanceRepository(pool)
    debit_code = await _seed_ledger_entry(pool)
    as_of = _clock() + timedelta(minutes=1)

    clean = await replay_verify.verify(pool, as_of=as_of, hours=1)
    assert clean.ok, clean.mismatches

    await _bump_balance_outside_event_trail(pool, debit_code, 1)
    tampered = await replay_verify.verify(pool, as_of=as_of, hours=1)
    assert not tampered.ok
    assert any(d.domain == "ledger" and d.key == debit_code for d in tampered.mismatches)

    async with pool.acquire() as conn, conn.transaction():
        report = await resync_account_balance(conn, debit_code, journal=journal, balances=balances)
    assert report.resynced is True
    assert report.actual_balance_before != report.replayed_balance

    # `resync_account_balance`'s commit above is the repair itself -- unlike
    # the tamper helper, there is nothing left to undo in a `finally` here;
    # a correct repair converges the row back to what the journal already
    # says, permanently (that convergence is exactly what this test proves).
    repaired = await replay_verify.verify(pool, as_of=as_of, hours=1)
    assert repaired.ok, repaired.mismatches


async def test_resync_second_call_after_repair_is_idempotent(pool):
    journal = PostgresJournalRepository(pool)
    balances = PostgresBalanceRepository(pool)
    debit_code = await _seed_ledger_entry(pool)

    await _bump_balance_outside_event_trail(pool, debit_code, 1)
    async with pool.acquire() as conn, conn.transaction():
        first = await resync_account_balance(conn, debit_code, journal=journal, balances=balances)
    assert first.resynced is True

    async with pool.acquire() as conn, conn.transaction():
        second = await resync_account_balance(conn, debit_code, journal=journal, balances=balances)
    assert second.resynced is False
    assert second.actual_balance_before == first.replayed_balance


async def test_resync_refuses_when_ledger_write_frozen(pool):
    """Negative + failure-injection: `ledger_control.write_frozen=true` must
    block the repair itself (fail-closed, same posture as
    `post_entry._assert_not_frozen`) and must not touch the row."""
    journal = PostgresJournalRepository(pool)
    balances = PostgresBalanceRepository(pool)
    debit_code = await _seed_ledger_entry(pool)
    await _bump_balance_outside_event_trail(pool, debit_code, 1)
    before = await _row(pool, debit_code)

    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE ledger_control SET write_frozen = TRUE, frozen_reason = 'test-5749' "
            "WHERE id = 1"
        )
        try:
            with pytest.raises(DriftResyncFrozenError):
                await resync_account_balance(conn, debit_code, journal=journal, balances=balances)
            assert await _row(pool, debit_code) == before
        finally:
            await conn.execute(
                "UPDATE ledger_control SET write_frozen = FALSE, frozen_reason = NULL WHERE id = 1"
            )
            await _bump_balance_outside_event_trail(pool, debit_code, -1)


async def test_resync_raises_for_unknown_account(pool):
    """Negative: an `account_code` this fold has never heard of is rejected,
    not silently treated as zero-balance/created (mirrors
    `BalanceRepository.get_for_update`'s `UnknownAccountError`)."""
    journal = PostgresJournalRepository(pool)
    balances = PostgresBalanceRepository(pool)
    unknown_code = user_account(uuid4(), UserSub.AVAILABLE)

    async with pool.acquire() as conn, conn.transaction():
        with pytest.raises(UnknownAccountError):
            await resync_account_balance(conn, unknown_code, journal=journal, balances=balances)


async def test_resync_raises_for_account_missing_its_balance_row(pool):
    """Negative: `account_code` is a real `ledger_account` row (so it has
    journal history to fold) but its `ledger_balance` row was removed --
    distinct from `test_resync_raises_for_unknown_account` (which never had
    an account at all). `BalanceRepository.get_for_update` joins
    `ledger_account`/`ledger_balance`, so an orphaned account is
    indistinguishable from an unknown one at that layer and must be
    rejected the same way, not silently treated as zero-balance."""
    journal = PostgresJournalRepository(pool)
    balances = PostgresBalanceRepository(pool)
    debit_code = await _seed_ledger_entry(pool)

    # Deleting the only `ledger_balance` row for a real, journal-backed
    # account is itself a permanent write against the shared, never-reset
    # TEST_DATABASE_URL (same caveat `_bump_balance_outside_event_trail`
    # carries) -- capture it and restore in `finally` so this test cannot
    # wedge a later `replay_verify` run the way the tampered-balance test
    # already guards against.
    async with pool.acquire() as conn:
        saved = await conn.fetchrow(
            "DELETE FROM ledger_balance WHERE account_id = "
            "(SELECT account_id FROM ledger_account WHERE account_code = $1) "
            "RETURNING account_id, balance, held, pending_payout, allow_negative,"
            " last_entry_seq, updated_at",
            debit_code,
        )
    assert saved is not None
    try:
        async with pool.acquire() as conn, conn.transaction():
            with pytest.raises(UnknownAccountError):
                await resync_account_balance(conn, debit_code, journal=journal, balances=balances)
    finally:
        # audit-allow: ledger_balance_raw_seed -- this restores the exact row
        # this same test deleted above (`saved` is that `DELETE ... RETURNING`),
        # not a fabricated balance. It is cleanup for a white-box test of the
        # repository/resync layer itself against the shared TEST_DATABASE_URL,
        # not account provisioning -- the values come from the prior real
        # journal-backed state, so there is no event-less balance left behind
        # for `replay_verify` to trip on.
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO ledger_balance ("
                " account_id, balance, held, pending_payout, allow_negative,"
                " last_entry_seq, updated_at"
                ") VALUES ($1, $2, $3, $4, $5, $6, $7)",
                saved["account_id"],
                saved["balance"],
                saved["held"],
                saved["pending_payout"],
                saved["allow_negative"],
                saved["last_entry_seq"],
                saved["updated_at"],
            )


@pytest.mark.perf
async def test_resync_completes_within_latency_budget_for_one_account_with_many_entries(pool):
    """Numeric performance assertion -- `compute_drift` folds the *entire*
    journal from sequence 1 every call (same unbounded-fold design
    `test_replay_verify.py::
    test_replay_verify_completes_within_latency_budget_for_fifty_streams`
    already budgets for), so a regression that makes the fold quadratic or
    re-fetches per-entry would show up here as wall-clock growth."""
    journal = PostgresJournalRepository(pool)
    balances = PostgresBalanceRepository(pool)
    debit_code = await _seed_ledger_entry(pool)
    for _ in range(25):
        await _seed_ledger_entry(pool)

    started = time.perf_counter()
    async with pool.acquire() as conn, conn.transaction():
        report = await compute_drift(conn, debit_code, journal=journal, balances=balances)
    elapsed_s = time.perf_counter() - started

    assert report.resynced is False
    assert elapsed_s < 5.0, f"compute_drift took {elapsed_s:.3f}s (budget 5.0s)"
