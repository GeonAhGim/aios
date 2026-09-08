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
from uuid import UUID, uuid4

import pytest

from scripts import replay_verify
from src.core.eventstore import replay
from src.core.eventstore.projections.orders import EventChainBrokenError
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
            "INSERT INTO ledger_balance (account_id, allow_negative, last_entry_seq) "
            "VALUES ($1, $2, 0)",
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
        encoding="utf-8",
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
        # audit-allow: ledger_balance_raw_seed -- FA-15a/esc-2115가 금지하는
        # 것은 초기 잔액 raw 시드다. 이건 DoD(2) "이벤트 트레일 밖 변조"를
        # 재현하는 adversarial tamper이지 시드가 아니다.
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


async def test_replay_digest_differs_between_accounts_with_different_balances(pool):
    """DoD(b), task-2394 -- falsifies the ci:77871f678ce2 symptom directly:
    every `USER:*:AVAILABLE` key reported the exact same two digests
    (`replayed`/`actual`), which is only possible if the digest input never
    actually varied with account balance. Two accounts posted with different
    amounts must fold to different `replayed` states and therefore different
    digests -- if `digest_state`'s input ever regresses to empty/constant
    (e.g. `_ledger_pairs` folding zero entries), this fails immediately
    instead of silently matching."""
    journal = PostgresJournalRepository(pool)

    async def _seed(amount: Decimal) -> str:
        debit_code = await _seed_ledger_account(pool, allow_negative=True)
        credit_code = await _seed_ledger_account(pool, allow_negative=False)
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
            await post_entry(
                conn, event, journal=journal, balances=PostgresBalanceRepository(pool),
                audit=PostgresAuditEventRepository(pool), clock=_clock,
            )
        return credit_code

    code_a = await _seed(Decimal("10.00"))
    code_b = await _seed(Decimal("25.00"))

    async with pool.acquire() as conn:
        pairs = await replay_verify._ledger_pairs(conn, journal, [code_a, code_b])

    replayed_a, actual_a = pairs[("ledger", code_a)]
    replayed_b, actual_b = pairs[("ledger", code_b)]
    assert replayed_a["balance"] != replayed_b["balance"]
    assert replay.digest_state(replayed_a) != replay.digest_state(replayed_b)
    assert replay.digest_state(actual_a) != replay.digest_state(actual_b)
    assert replay.digest_state(replayed_a) == replay.digest_state(actual_a)
    assert replay.digest_state(replayed_b) == replay.digest_state(actual_b)


class _DiscardTransaction(Exception):
    """Sentinel to force `conn.transaction()` to roll back on a clean pass."""


async def test_replay_flags_order_status_changed_without_event_as_mismatch(pool):
    """task-2394 -- reproduces the write-path gap behind ci:77871f678ce2's
    'orders 1건' mismatch: `src/services/safety/open_order_sweeper.py`'s
    `sweep_open_orders()` does `UPDATE orders SET status = 'CANCEL_REQUESTED'
    ...` directly, with no matching `order_events` row (its docstring's
    single-statement-UPDATE design is deliberate -- see task note). The
    order's event chain is not *broken* (task-2173's pre-cutover carve-out
    doesn't apply here -- every recorded event is complete and in order,
    unlike `_seed_broken_chain_order`'s mid-chain gap); it is *incomplete*:
    replay stops at SUBMITTED while `orders.status` silently moved on.
    replay_verify must report that as a real mismatch, not silently pass --
    fail-closed is the entire point of FA-15/FA-16.

    Everything after `order_id` runs inside one explicit transaction that is
    always rolled back (`_DiscardTransaction`), never committed -- `orders`
    has no WORM guard but `073beca589d5`'s I5 trigger unconditionally
    auto-increments `version` on *every* UPDATE (cutover or not), so even a
    "restore status" cleanup UPDATE desyncs `version` from what replay would
    fold and leaves the row permanently mismatching. A first version of this
    test tried exactly that revert-via-UPDATE cleanup and it visibly poisoned
    three sibling tests' `hours=1` windows in the same run (all started
    failing on the leftover order) -- a live, small-scale rerun of this same
    task's CI symptom. Rollback is the only cleanup that actually leaves zero
    trace, so `replay_verify._order_pair` is called directly on this
    transaction's own connection (not `verify()`/`pool.acquire()`, which
    would open a second connection and never see the uncommitted rows)."""
    user_id = await create_test_user(pool)
    repo = PostgresOrderRepository()
    async with pool.acquire() as conn:
        try:
            async with conn.transaction():
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
                await repo.transition(
                    conn,
                    order_id=order_id,
                    expected_status=OrderStatus.VALIDATED,
                    expected_version=1,
                    new_status=OrderStatus.SUBMITTED,
                    patch={},
                    event=_order_event(
                        order_id,
                        from_status=OrderStatus.VALIDATED,
                        to_status=OrderStatus.SUBMITTED,
                        event="SENT",
                    ),
                )
                # Mirrors open_order_sweeper.sweep_open_orders()'s bulk cancel
                # UPDATE exactly -- no SET LOCAL oms.event_written, no
                # order_events row.
                await conn.execute(
                    "UPDATE orders SET status = 'CANCEL_REQUESTED', updated_at = now() "
                    "WHERE order_id = $1",
                    order_id,
                )

                cutover_at = await replay_verify._cutover_at(conn)
                pair = await replay_verify._order_pair(conn, order_id, cutover_at=cutover_at)

                assert pair is not None, "chain is complete, not broken -- must not be skipped"
                replayed, actual = pair
                assert replayed["status"] != actual["status"]
                assert replay.digest_state(replayed) != replay.digest_state(actual)

                raise _DiscardTransaction
        except _DiscardTransaction:
            pass


async def _seed_broken_chain_order(pool) -> UUID:
    """A raw-seeded order (status set directly at INSERT, bypassing
    order_events entirely -- the pattern several OMS test fixtures use to
    start a test mid-lifecycle) plus one real transition. The resulting
    `order_events` timeline has exactly one row whose `from_status` is
    VALIDATED, not CREATED -- the same shape 753a88c6aeb5's CI run hit
    (order 70d76b11-..., seq=21, from_status=VALIDATED)."""
    user_id = await create_test_user(pool)
    async with pool.acquire() as conn:
        order_id = await insert_order(conn, user_id, status="VALIDATED")
        async with conn.transaction():
            # `SET LOCAL` is transaction-scoped -- I6 (073beca589d5) only sees
            # `oms.event_written='1'` if the append and the UPDATE share the
            # same tx, exactly like every real `transition()` caller wraps it
            # (submit_order.py/outbox_dispatcher.py, `conn.transaction()`).
            await PostgresOrderRepository().transition(
                conn,
                order_id=order_id,
                expected_status=OrderStatus.VALIDATED,
                expected_version=0,
                new_status=OrderStatus.SUBMITTED,
                patch={},
                event=_order_event(
                    order_id,
                    from_status=OrderStatus.VALIDATED,
                    to_status=OrderStatus.SUBMITTED,
                    event="SENT",
                ),
            )
    return order_id


async def test_replay_skips_pre_cutover_order_with_broken_event_chain(pool):
    """task-2173 -- `oms_order_transition_cutover.cutover_at` is NULL
    (unarmed) by default in this test DB, so 073beca589d5's I6 trigger never
    required this order to carry a complete `order_events` trail. Before the
    fix, `orders_projection.project()`'s `EventChainBrokenError` propagated
    all the way out of `verify()` and crashed the script; now `_order_pair`
    recognizes the order predates any armed cutover and skips it instead of
    crashing or reporting a false mismatch."""
    order_id = await _seed_broken_chain_order(pool)
    as_of = _clock() + timedelta(minutes=1)

    report = await replay_verify.verify(pool, as_of=as_of, hours=1)

    assert report.ok, report.mismatches
    assert not any(d.key == str(order_id) for d in report.mismatches)


async def test_replay_still_raises_for_post_cutover_broken_event_chain(pool):
    """Negative test for the task-2173 fix itself -- once cutover is armed,
    073beca589d5's I6 trigger makes a broken chain structurally impossible
    for any real write path, so a broken chain on an order created at/after
    the armed cutover must still fail closed (not be silently skipped the
    way a pre-cutover order is)."""
    async with pool.acquire() as conn:
        # `cutover_at = now()` (not further back) so this only pulls *this*
        # test's own order into I6 scope -- other tests in this same
        # session may have left pre-cutover broken-chain orders committed
        # in the last hour (test_replay_skips_pre_cutover_..._chain does),
        # and those must stay out of scope or this test would catch the
        # wrong order's EventChainBrokenError.
        armed = await conn.fetchval(
            "UPDATE oms_order_transition_cutover SET cutover_at = now(), "
            "armed_by = 'test-2173' WHERE id = 1 AND cutover_at IS NULL RETURNING cutover_at"
        )
    assert armed is not None, "cutover already armed by another test run -- refusing to clobber it"
    try:
        order_id = await _seed_broken_chain_order(pool)
        as_of = _clock() + timedelta(minutes=1)

        with pytest.raises(EventChainBrokenError) as excinfo:
            await replay_verify.verify(pool, as_of=as_of, hours=1)
        assert excinfo.value.order_id == order_id
    finally:
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE oms_order_transition_cutover SET cutover_at = NULL, armed_by = NULL "
                "WHERE id = 1"
            )
