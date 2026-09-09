"""task-2432 DoD(d) — FA-16 `replay_verify` orders projection folds the real
(no longer self-loop) `CANCEL_REQUESTED` transition `open_order_sweeper.py`
now writes, byte-identical to the actual row; a tampered/incomplete event
trail is still caught as a mismatch.

Spec: docs/specs/execution_oms_and_exchange.md#§2-C 상태기계 표 · §9 L4-06 +
docs/specs/ibor_fund_accounting_and_resilience.md#§9 FA-16.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from scripts import replay_verify
from src.core.eventstore import replay
from src.data.models.trading import OrderStatus
from src.foundation.risk_gate.domain.models import SafetyScope
from src.services.oms.adapters.order_repository import PostgresOrderRepository
from src.services.oms.contracts.v1_events import OrderTransitionEvent
from src.services.safety.open_order_sweeper import sweep_open_orders
from tests.integration.conftest import create_test_user
from tests.integration.fake_exchange_adapter import FakeExchangeAdapter
from tests.integration.oms.conftest import insert_order


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


async def _to_submitted_via_complete_chain(conn, order_id: UUID) -> None:
    """A real CREATED->VALIDATED->SUBMITTED chain on an already-inserted
    (CREATED) order -- `orders_projection.project()` requires the fold to
    start at CREATED (`_INITIAL_STATUS`), so seeding straight at SUBMITTED
    would make the first order_events row's `from_status` disagree with the
    folded state and get treated as a broken (pre-cutover, skipped) chain
    instead of the clean byte-match these tests need."""
    repo = PostgresOrderRepository()
    await repo.transition(
        conn,
        order_id=order_id,
        expected_status=OrderStatus.CREATED,
        expected_version=0,
        new_status=OrderStatus.VALIDATED,
        patch={},
        event=_order_event(
            order_id, from_status=OrderStatus.CREATED, to_status=OrderStatus.VALIDATED,
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
            order_id, from_status=OrderStatus.VALIDATED, to_status=OrderStatus.SUBMITTED,
            event="SENT",
        ),
    )


async def test_replay_verify_byte_matches_sweeper_cancel_requested_event(pool):
    """Positive half of DoD(d) — after the fix, `sweep_open_orders`'s
    real (non-self-loop) SUBMITTED->CANCEL_REQUESTED event replays to
    exactly the row it wrote."""
    user_id = await create_test_user(pool)
    async with pool.acquire() as conn:
        order_id = await insert_order(conn, user_id, status="CREATED")
        await _to_submitted_via_complete_chain(conn, order_id)

    report = await sweep_open_orders(
        pool,
        {"bitget": FakeExchangeAdapter(exchange_name="bitget")},
        control_id=uuid4(),
        scope=SafetyScope.TENANT,
        scope_ref=str(user_id),
    )
    assert report.cancel_requested == (order_id,)

    as_of = _clock() + timedelta(minutes=1)
    result = await replay_verify.verify(pool, as_of=as_of, hours=1)
    assert result.ok, result.mismatches
    assert result.streams_checked >= 1

    async with pool.acquire() as conn:
        cutover_at = await replay_verify._cutover_at(conn)
        pair = await replay_verify._order_pair(conn, order_id, cutover_at=cutover_at)
    assert pair is not None
    replayed, actual = pair
    assert replayed == actual
    assert replayed["status"] == "CANCEL_REQUESTED"


class _DiscardTransaction(Exception):
    """Sentinel to force `conn.transaction()` to roll back -- see
    `test_replay_flags_order_status_changed_without_event_as_mismatch` in
    tests/integration/eventstore/test_replay_verify.py for why: 073beca589d5's
    I5 trigger bumps `orders.version` unconditionally on every UPDATE, so even
    a revert-via-UPDATE cleanup would permanently desync `version` and poison
    every later `hours=1` replay_verify window in this shared test DB."""


async def test_cancel_requested_event_tamper_is_flagged_as_replay_mismatch(pool):
    """Negative/reproduction half of DoD(d) -- a genuine SUBMITTED ->
    CANCEL_REQUESTED transition (the shape `sweep_open_orders` now writes)
    replays byte-identical; if `orders.status` is bumped again afterwards
    without a companion `order_events` row (the exact FA-16/I-10 violation
    this leaf makes real and catchable for CANCEL_REQUESTED, previously
    hidden behind the self-loop workaround), `replay_verify` must flag it
    instead of silently passing."""
    user_id = await create_test_user(pool)
    repo = PostgresOrderRepository()
    async with pool.acquire() as conn:
        try:
            async with conn.transaction():
                order_id = await insert_order(conn, user_id, status="CREATED")
                await _to_submitted_via_complete_chain(conn, order_id)
                await repo.transition(
                    conn,
                    order_id=order_id,
                    expected_status=OrderStatus.SUBMITTED,
                    expected_version=2,
                    new_status=OrderStatus.CANCEL_REQUESTED,
                    patch={},
                    event=_order_event(
                        order_id,
                        from_status=OrderStatus.SUBMITTED,
                        to_status=OrderStatus.CANCEL_REQUESTED,
                        event="CANCEL_REQUESTED",
                    ),
                )

                cutover_at = await replay_verify._cutover_at(conn)
                clean_pair = await replay_verify._order_pair(conn, order_id, cutover_at=cutover_at)
                assert clean_pair is not None, "chain is complete -- must not be skipped"
                clean_replayed, clean_actual = clean_pair
                assert clean_replayed == clean_actual
                assert replay.digest_state(clean_replayed) == replay.digest_state(clean_actual)

                # Tamper: bump status again with no companion order_events row
                # (bypasses I6/oms.event_written on purpose).
                await conn.execute(
                    "UPDATE orders SET status = 'CANCELLED', updated_at = now() "
                    "WHERE order_id = $1",
                    order_id,
                )
                tampered_pair = await replay_verify._order_pair(
                    conn, order_id, cutover_at=cutover_at
                )
                assert tampered_pair is not None
                tampered_replayed, tampered_actual = tampered_pair
                assert tampered_replayed["status"] != tampered_actual["status"]
                assert replay.digest_state(tampered_replayed) != replay.digest_state(
                    tampered_actual
                )

                raise _DiscardTransaction
        except _DiscardTransaction:
            pass
