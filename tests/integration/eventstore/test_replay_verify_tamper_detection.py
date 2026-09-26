"""FA-15 integration test -- `scripts/replay_verify.py` DoD(2) wiring proof.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#9 FA-15 DoD.

Tampering with rows outside the event trail (`ledger_balance`, `orders`)
must make the checker actually fail, both as a pure `verify()` call and as
the real `scripts/replay_verify.py` subprocess exit code -- an always-exit-0
checker would pass DoD(1) alone (see `test_replay_verify.py`) but not this.
Also covers the pre/post-cutover broken-event-chain handling (task-2173).

Split from `test_replay_verify.py` at task-7810 (7e9cc090/task-7695 pushed
that file to 583 lines, check_code_ratchets.py loc_over_500 gate). Shared
fixtures/helpers are in `_replay_verify_support.py`.
"""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID

import asyncpg
import pytest

from scripts import replay_verify
from src.core.eventstore import replay
from src.core.eventstore.projections.orders import EventChainBrokenError
from src.data.models.trading import OrderStatus
from src.services.oms.adapters.order_repository import PostgresOrderRepository
from tests.integration.conftest import create_test_user
from tests.integration.eventstore._replay_verify_support import (
    _clock,
    _order_event,
    _run_script,
    _seed_ledger_entry,
)
from tests.integration.oms.conftest import insert_order


@pytest.fixture
async def pool(isolated_replay_pool: asyncpg.Pool) -> asyncpg.Pool:
    """Module-isolated clone -- see `isolated_replay_db_url` in conftest.py."""
    return isolated_replay_pool


@pytest.fixture
def database_url(isolated_replay_db_url: str) -> str:
    """The `scripts/replay_verify.py` subprocess must verify the same clone the
    in-process assertions use, not the shared worker DB."""
    return isolated_replay_db_url


class _DiscardTransaction(Exception):
    """Sentinel to force `conn.transaction()` to roll back on a clean pass."""


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


async def test_replay_detects_order_filled_quantity_tampered_outside_the_event_trail(pool):
    """Negative test extending the FA-15 invariant beyond `status` (already
    covered by test_replay_flags_order_status_changed_without_event_as_mismatch
    and the ledger-side tamper test above) to another `_ORDER_FIELDS` entry --
    `filled_quantity` (not literally `fee_total`: `orders.fee_total` has no
    column default and `insert_order`/`transition` never set it, so it stays
    SQL NULL and `NULL + 1` folds back to NULL -- a no-op that would make
    this test pass vacuously; `filled_quantity` defaults to a real `0` and
    is one of the same `_ORDER_FIELDS`, so it exercises the identical gap).
    A raw UPDATE that only touches this column does not change `status`, so
    073beca589d5's I6 guard (armed only inside the `OLD.status IS DISTINCT
    FROM NEW.status` branch) never fires and the write succeeds silently --
    replay must still flag it, or FA-15 only ever proves the `status` column
    agrees, not the row it claims to verify.

    Like test_replay_flags_order_status_changed_without_event_as_mismatch,
    the tamper runs inside a transaction that is always rolled back
    (`_DiscardTransaction`), never committed: 073beca589d5's I5 trigger
    unconditionally bumps `version` on *every* UPDATE (fee-only or not), so
    a commit-then-restore-fee_total cleanup would still leave `version`
    permanently desynced from what replay folds -- rollback is the only
    cleanup that leaves zero trace. `_order_pair` is called directly on this
    transaction's own connection so it sees the uncommitted row.
    """
    user_id = await create_test_user(pool)
    async with pool.acquire() as conn:
        try:
            async with conn.transaction():
                order_id = await insert_order(conn, user_id, status="CREATED")
                await PostgresOrderRepository().transition(
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

                cutover_at = await replay_verify._cutover_at(conn)
                clean_pair = await replay_verify._order_pair(conn, order_id, cutover_at=cutover_at)
                assert clean_pair is not None
                clean_replayed, clean_actual = clean_pair
                assert replay.digest_state(clean_replayed) == replay.digest_state(clean_actual)

                await conn.execute(
                    "UPDATE orders SET filled_quantity = filled_quantity + 1 WHERE order_id = $1",
                    order_id,
                )

                tampered_pair = await replay_verify._order_pair(
                    conn, order_id, cutover_at=cutover_at
                )
                assert tampered_pair is not None
                tampered_replayed, tampered_actual = tampered_pair
                assert tampered_replayed["filled_quantity"] != tampered_actual["filled_quantity"]
                assert replay.digest_state(tampered_replayed) != replay.digest_state(
                    tampered_actual
                )

                raise _DiscardTransaction
        except _DiscardTransaction:
            pass


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
