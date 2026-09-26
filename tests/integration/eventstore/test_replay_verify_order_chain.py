"""FA-15 order-event-chain mismatch/cutover regressions -- split out of
`test_replay_verify.py` to stay under the 500-line file-policy observation
line (ADR-2026-09-10-C §7); shared DoD/ledger-side coverage and helpers
(`_clock`, `_order_event`, `_seed_order`, `replay_verify`/`replay` imports)
live there.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#9 FA-15 DoD.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import asyncpg
import pytest

from scripts import replay_verify
from src.core.eventstore import replay
from src.core.eventstore.projections.orders import EventChainBrokenError
from src.data.models.trading import OrderStatus
from src.services.oms.adapters.order_repository import PostgresOrderRepository
from src.services.oms.contracts.v1_events import OrderTransitionEvent
from tests.integration.conftest import create_test_user
from tests.integration.oms.conftest import insert_order
from tests.support.db import _asyncpg_dsn, ensure_worker_database, template_database_url


@pytest.fixture
async def pool() -> AsyncIterator[asyncpg.Pool]:
    """Dedicated database for this module (same pattern as
    tests/integration/oms/test_cancel_requested_replay.py, fe50dcd4).
    `replay_verify.verify(hours=1)` digests *every* order stream touched inside
    the window, so an order another file left in the shared xdist worker DB
    with a status changed outside the event trail (the very shape
    test_replay_flags_order_status_changed_without_event_as_mismatch seeds)
    shows up as a mismatch here (PR #91 run 36222175795: StreamDiff on an
    order this module never created). Cloning from the untouched template keeps
    the assertion about this module's own writes; the template URL (not the live
    worker DB) avoids ObjectInUseError while other sessions are open."""
    worker = os.environ.get("PYTEST_XDIST_WORKER", "master")
    url = await ensure_worker_database(template_database_url(), f"{worker}_replay_chain")
    p = await asyncpg.create_pool(_asyncpg_dsn(url), min_size=1, max_size=4)
    try:
        yield p
    finally:
        await p.close()


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


class _DiscardTransaction(Exception):
    """Sentinel to force `conn.transaction()` to roll back on a clean pass."""


async def test_replay_detects_order_filled_quantity_tampered_outside_the_event_trail(pool):
    """Negative test extending the FA-15 invariant beyond `status` (already
    covered by test_replay_flags_order_status_changed_without_event_as_mismatch
    and the ledger-side tamper test in test_replay_verify.py) to another
    `_ORDER_FIELDS` entry -- `filled_quantity` (not literally `fee_total`:
    `orders.fee_total` has no column default and `insert_order`/`transition`
    never set it, so it stays SQL NULL and `NULL + 1` folds back to NULL --
    a no-op that would make this test pass vacuously; `filled_quantity`
    defaults to a real `0` and is one of the same `_ORDER_FIELDS`, so it
    exercises the identical gap). A raw UPDATE that only touches this column
    does not change `status`, so 073beca589d5's I6 guard (armed only inside
    the `OLD.status IS DISTINCT FROM NEW.status` branch) never fires and the
    write succeeds silently -- replay must still flag it, or FA-15 only ever
    proves the `status` column agrees, not the row it claims to verify.

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
