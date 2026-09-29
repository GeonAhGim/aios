"""task-8661 -- FA-16 `replay_verify` orders projection accounts for the
silent `orders.version` bump `set_committed_child_qty` causes (EM-3 child
slice reserve/release), the same class of gap already solved for FILL in
`core/eventstore/projections/orders.py` (see that module's docstring).

Root cause: `073beca589d5`'s `oms_enforce_order_transition_trg` bumps
`orders.version` unconditionally on every `UPDATE orders` (I5), including
the `committed_child_qty`-only updates `reserve_child_slice`/
`release_reserved_slice` make -- but until this fix those updates carried no
`order_events` row, so the pure fold in `orders.py::apply_one()` had no way
to know about that bump. Any parent order that ever reserved/released a
child slice therefore replayed to a `version` permanently behind the actual
row, a deterministic (not flaky) `replay_verify` MISMATCH on `version` alone
that survives even though the event chain never breaks.

Spec: docs/specs/L4_ems_routing_algos_and_tca_v1.0.md #9 EM-3 +
docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#§9 FA-16.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import asyncpg
import pytest

from scripts import replay_verify
from src.core.eventstore import replay
from src.data.models.trading import OrderStatus
from src.foundation.ems.application.aggregate_parent import (
    release_reserved_slice,
    reserve_child_slice,
)
from src.services.oms.adapters.order_repository import PostgresOrderRepository
from src.services.oms.contracts.v1_events import OrderTransitionEvent
from tests.integration.oms.conftest import create_test_user, insert_order
from tests.support.db import _asyncpg_dsn, ensure_worker_database


@pytest.fixture
async def pool() -> AsyncIterator[asyncpg.Pool]:
    """Dedicated database for this module -- same isolation rationale as
    `tests/integration/oms/test_cancel_requested_replay.py`'s `pool` fixture
    (`replay_verify.verify()` digests every stream touched inside the
    window, so a shared worker DB would pick up other files' leftovers)."""
    worker = os.environ.get("PYTEST_XDIST_WORKER", "master")
    url = await ensure_worker_database(os.environ["TEST_DATABASE_URL"], f"{worker}_child_qty")
    p = await asyncpg.create_pool(_asyncpg_dsn(url), min_size=1, max_size=4)
    try:
        yield p
    finally:
        await p.close()


def _clock() -> datetime:
    return datetime.now(timezone.utc)


def _order_event(
    order_id: UUID, *, from_status: OrderStatus, to_status: OrderStatus, event: str
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


async def _to_acknowledged_via_complete_chain(conn, order_id: UUID) -> None:
    """A real CREATED->VALIDATED->SUBMITTED->ACKNOWLEDGED chain --
    `orders_projection.project()` requires the fold to start at CREATED, so
    seeding straight at ACKNOWLEDGED would make the first order_events row's
    `from_status` disagree with the folded state and get treated as a broken
    (pre-cutover, skipped) chain instead of the clean byte-match this test
    needs (same reasoning as `test_cancel_requested_replay.py`'s helper)."""
    repo = PostgresOrderRepository()
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
        patch={"exchange_order_id": f"ex-{uuid4().hex[:12]}"},
        event=_order_event(
            order_id,
            from_status=OrderStatus.VALIDATED,
            to_status=OrderStatus.SUBMITTED,
            event="SENT",
        ),
    )
    await repo.transition(
        conn,
        order_id=order_id,
        expected_status=OrderStatus.SUBMITTED,
        expected_version=2,
        new_status=OrderStatus.ACKNOWLEDGED,
        patch={},
        event=_order_event(
            order_id,
            from_status=OrderStatus.SUBMITTED,
            to_status=OrderStatus.ACKNOWLEDGED,
            event="ACK",
        ),
    )


async def test_replay_verify_byte_matches_after_reserve_and_release_child_slice(pool):
    """Positive case -- a parent order that reserves then releases a child
    slice (EM-A1/EM-A4) still replays byte-identical to the actual row: the
    `CHILD_QTY_COMMITTED` self-loop event this fix adds accounts for both
    silent `version` bumps."""
    user_id = await create_test_user(pool)
    repo = PostgresOrderRepository()
    async with pool.acquire() as conn:
        parent_id = await insert_order(conn, user_id, status="CREATED", quantity=Decimal("10"))
        await _to_acknowledged_via_complete_chain(conn, parent_id)

        await reserve_child_slice(repo, conn, parent_order_id=parent_id, new_slice_qty=Decimal("4"))
        await release_reserved_slice(repo, conn, parent_order_id=parent_id, slice_qty=Decimal("4"))

    as_of = _clock() + timedelta(minutes=1)
    result = await replay_verify.verify(pool, as_of=as_of, hours=1)
    assert result.ok, result.mismatches
    assert result.streams_checked >= 1

    async with pool.acquire() as conn:
        cutover_at = await replay_verify._cutover_at(conn)
        pair = await replay_verify._order_pair(conn, parent_id, cutover_at=cutover_at)
    assert pair is not None
    replayed, actual = pair
    assert replayed == actual
    assert replayed["version"] == actual["version"] == 5  # 3 real transitions + 2 self-loops


class _DiscardTransaction(Exception):
    """Sentinel to force `conn.transaction()` to roll back -- same rationale
    as `test_cancel_requested_replay.py`'s sentinel of the same name: I5
    bumps `version` unconditionally even on a revert-via-UPDATE cleanup, so
    that would permanently desync `version` and poison later windows in this
    shared test DB."""


async def test_committed_child_qty_bump_without_event_is_flagged_as_replay_mismatch(pool):
    """Negative/reproduction case -- this is the pre-fix defect made real:
    if `orders.committed_child_qty`/`version` were bumped (the I5 trigger
    always bumps `version` on any `UPDATE orders`) with no companion
    `order_events` row, `replay_verify` must flag it instead of silently
    passing."""
    user_id = await create_test_user(pool)
    async with pool.acquire() as conn:
        try:
            async with conn.transaction():
                parent_id = await insert_order(
                    conn, user_id, status="CREATED", quantity=Decimal("10")
                )
                await _to_acknowledged_via_complete_chain(conn, parent_id)

                cutover_at = await replay_verify._cutover_at(conn)
                clean_pair = await replay_verify._order_pair(conn, parent_id, cutover_at=cutover_at)
                assert clean_pair is not None, "chain is complete -- must not be skipped"
                clean_replayed, clean_actual = clean_pair
                assert clean_replayed == clean_actual

                # Tamper: reproduce the pre-fix bug directly -- bump
                # committed_child_qty (and, via I5, version) with no
                # companion order_events row.
                await conn.execute(
                    "UPDATE orders SET committed_child_qty = 4, updated_at = now() "
                    "WHERE order_id = $1",
                    parent_id,
                )
                tampered_pair = await replay_verify._order_pair(
                    conn, parent_id, cutover_at=cutover_at
                )
                assert tampered_pair is not None
                tampered_replayed, tampered_actual = tampered_pair
                assert tampered_replayed["version"] != tampered_actual["version"]
                assert replay.digest_state(tampered_replayed) != replay.digest_state(
                    tampered_actual
                )

                raise _DiscardTransaction
        except _DiscardTransaction:
            pass
