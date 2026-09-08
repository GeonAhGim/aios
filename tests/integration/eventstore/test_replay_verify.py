"""FA-15 integration test -- `scripts/replay_verify.py` against a real DB.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#9 FA-15 DoD.

DoD(1) a clean order + ledger entry replay byte-identical to their current
table rows. DoD(2) wiring proof -- tampering with `ledger_balance` (the
*actual* row, since `order_events`/`ledger_journal_entry` are WORM-protected
-- src/core/db/append_only.py's `BEFORE UPDATE` trigger fires even for the
table owner, so a test cannot literally `UPDATE` an event row) makes the
checker actually fail, both as a pure `verify()` call and as the real
`scripts/replay_verify.py` subprocess exit code -- an always-exit-0 checker
would pass DoD(1) alone but not this. DoD(3) replaying the same window twice
yields the same `combined_digest` (no clock/dict-order dependence).
"""
from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

from scripts import replay_verify
from src.data.models.base import Currency
from src.data.models.trading import OrderStatus
from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.ledger.adapters.postgres_balance_repository import PostgresBalanceRepository
from src.foundation.ledger.adapters.postgres_journal_repository import PostgresJournalRepository
from src.foundation.ledger.application.post_entry import post_entry
from src.foundation.ledger.contracts.v1 import AccountType, LedgerEvent, LedgerEventType, UserSub
from src.foundation.ledger.domain.chart_of_accounts import user_account
from src.services.oms.adapters.order_repository import PostgresOrderRepository
from src.services.oms.contracts.v1_events import OrderTransitionEvent
from tests.integration.conftest import create_test_user
from tests.integration.oms.conftest import insert_order

_REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def database_url() -> str:
    return os.environ["DATABASE_URL"]


def _clock() -> datetime:
    return datetime.now(timezone.utc)


def _order_event(
    order_id, *, from_status: OrderStatus, to_status: OrderStatus, event: str
) -> OrderTransitionEvent:
    return OrderTransitionEvent(
        order_id=order_id,
        from_status=from_status,
        to_status=to_status,
        event=event,
        reason_code=None,
        actor_subject_id="system",
        trace_id=uuid4(),
        command_id=None,
        provider_event_id=None,
        occurred_at=_clock(),
        payload_hash="e" * 64,
    )


async def _seed_order(pool) -> None:
    repo = PostgresOrderRepository()
    user_id = await create_test_user(pool)
    async with pool.acquire() as conn:
        order_id = await insert_order(conn, user_id, status="CREATED")
        await repo.transition(
            conn,
            order_id=order_id,
            expected_status=OrderStatus.CREATED,
            expected_version=0,
            new_status=OrderStatus.VALIDATED,
            patch={},
            event=_order_event(
                order_id,
                from_status=OrderStatus.CREATED,
                to_status=OrderStatus.VALIDATED,
                event="VALIDATED",
            ),
        )


async def _seed_ledger_account(pool, *, allow_negative: bool) -> str:
    code = user_account(uuid4(), UserSub.RECEIVABLE if allow_negative else UserSub.AVAILABLE)
    async with pool.acquire() as conn:
        account_id = await conn.fetchval(
            "INSERT INTO ledger_account (account_code, account_type, currency, allow_negative) "
            "VALUES ($1, $2, $3, $4) RETURNING account_id",
            code,
            (AccountType.ASSET if allow_negative else AccountType.LIABILITY).value,
            Currency.KRW.value,
            allow_negative,
        )
        await conn.execute(
            "INSERT INTO ledger_balance (account_id, balance, allow_negative, last_entry_seq) "
            "VALUES ($1, 0, $2, 0)",
            account_id,
            allow_negative,
        )
    return code


async def _seed_ledger_entry(pool) -> str:
    """One MANUAL_ADJUSTMENT between two fresh accounts -- debit_code is
    what the test asserts on, mirroring test_projections.py's precedent."""
    journal = PostgresJournalRepository(pool)
    balances = PostgresBalanceRepository(pool)
    audit = PostgresAuditEventRepository(pool)
    debit_code = await _seed_ledger_account(pool, allow_negative=True)
    credit_code = await _seed_ledger_account(pool, allow_negative=False)

    event = LedgerEvent(
        event_type=LedgerEventType.MANUAL_ADJUSTMENT,
        event_ref=f"adj:{uuid4().hex}",
        tenant_id=None,
        actor_subject_id=None,
        trace_id=uuid4(),
        amount=Decimal("10.00"),
        currency=Currency.KRW,
        parties={},
        extra={"debit_account": debit_code, "credit_account": credit_code},
    )
    async with pool.acquire() as conn, conn.transaction():
        await post_entry(conn, event, journal=journal, balances=balances, audit=audit, clock=_clock)
    return debit_code


def _run_script(
    *, hours: int, as_of: datetime, database_url: str
) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "DATABASE_URL": database_url, "PYTHONPATH": str(_REPO_ROOT)}
    return subprocess.run(
        [
            sys.executable,
            str(_REPO_ROOT / "scripts" / "replay_verify.py"),
            "--hours",
            str(hours),
            "--as-of",
            as_of.isoformat(),
        ],
        cwd=_REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


async def test_replay_matches_current_tables_for_orders_and_ledger(pool):
    await _seed_order(pool)
    debit_code = await _seed_ledger_entry(pool)
    as_of = _clock() + timedelta(minutes=1)

    report = await replay_verify.verify(pool, as_of=as_of, hours=1)

    assert report.ok, report.mismatches
    assert report.streams_checked >= 2
    assert debit_code  # sanity: the seeded ledger account was actually created


async def test_replay_is_deterministic_across_repeated_runs(pool):
    await _seed_order(pool)
    await _seed_ledger_entry(pool)
    as_of = _clock() + timedelta(minutes=1)

    first = await replay_verify.verify(pool, as_of=as_of, hours=1)
    second = await replay_verify.verify(pool, as_of=as_of, hours=1)

    assert first.combined_digest == second.combined_digest
    assert first.streams_checked == second.streams_checked


async def test_replay_detects_ledger_balance_tampered_outside_the_event_trail(pool, database_url):
    """`ledger_journal_entry`/`ledger_posting_line` are WORM -- the only way
    to produce "actual != replayed" against a real DB is to corrupt the
    *derived* `ledger_balance` row directly (exactly the untracked-state-
    change scenario FA-16 will later block at write time; FA-15 catches it
    after the fact via replay)."""
    debit_code = await _seed_ledger_entry(pool)
    as_of = _clock() + timedelta(minutes=1)

    clean = await replay_verify.verify(pool, as_of=as_of, hours=1)
    assert clean.ok, clean.mismatches

    clean_run = _run_script(hours=1, as_of=as_of, database_url=database_url)
    assert clean_run.returncode == 0, clean_run.stdout + clean_run.stderr

    async def _bump_balance(delta: int) -> None:
        # FA-10 (a2c4f9e1b3d5) put a no-UPDATE trigger on `ledger_balance` --
        # a literal UPDATE now raises "no-update violation" instead of
        # mutating the row. Reproduce the tamper the same way the real
        # write path (`PostgresBalanceRepository.apply`) is forced to:
        # DELETE the current row, INSERT a replacement with the same key,
        # skipping `post_entry`/the event trail entirely -- that omission
        # (not the DELETE+INSERT mechanics) is what the test is exercising.
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
                debit_code,
            )

    # `ledger_balance` is mutable (not WORM), but a committed corruption here
    # would wedge every later CI run's own `replay_verify` step (local_ci.py)
    # permanently red -- always undo it, even if an assertion below fails.
    await _bump_balance(1)
    try:
        tampered = await replay_verify.verify(pool, as_of=as_of, hours=1)
        assert not tampered.ok
        assert any(d.domain == "ledger" and d.key == debit_code for d in tampered.mismatches)

        tampered_run = _run_script(hours=1, as_of=as_of, database_url=database_url)
        assert tampered_run.returncode == 1, tampered_run.stdout + tampered_run.stderr
        assert debit_code in tampered_run.stderr
    finally:
        await _bump_balance(-1)
