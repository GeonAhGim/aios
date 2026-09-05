"""L4-14 DoD — 리스를 잃은(stale) 워커의 늦은 쓰기는 거부된다.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §5.1 "outbox done/retry/dead
`WHERE id=$1 AND state='SENDING' AND worker_id=$2` RETURNING(리스 잃은 워커의 늦은
쓰기 차단)", §6 F2(lease 만료 → 복구가 UNKNOWN 처리), 105번 §2.

시나리오: 워커 A가 행을 SENDING으로 선점하고 거래소 호출이 길어진다. 그 사이
복구 워커(F2)가 lease 만료를 보고 주문을 UNKNOWN, 행을 DONE으로 닫는다. A의
응답이 뒤늦게 도착해도 (1) outbox 펜스가 0행 → `ConcurrencyConflictError`,
(2) 주문 전이는 같은 tx라 실행되지 않는다. 포트 대역 위 증명이며 실DB 변형은
L4-06/08 이후 추가(task-1538 note).
"""
from __future__ import annotations

import asyncio
from datetime import timedelta

from src.data.models.trading import Order, OrderStatus
from tests.support.oms_outbox_fakes import (
    FixedClock,
    InMemoryOrderRepo,
    InMemoryOutboxRepo,
    ScriptedAdapter,
    enqueue,
    make_dispatcher,
    make_order_view,
)


class _Gate:
    """전송 시작 신호 + 응답 보류 — 테스트가 그 사이에 복구를 끼워 넣는다."""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def place(self, order: Order) -> Order:
        self.started.set()
        await self.release.wait()
        return order.model_copy(
            update={"exchange_order_id": "ex-late", "status": OrderStatus.SUBMITTED}
        )


async def _start_stale_worker(clock: FixedClock):
    outbox, orders = InMemoryOutboxRepo(clock=clock), InMemoryOrderRepo()
    view = orders.add(make_order_view())
    row_id = await enqueue(outbox, view)
    gate = _Gate()
    adapter = ScriptedAdapter(on_place=gate.place)
    worker_a = make_dispatcher(
        outbox=outbox, orders=orders, adapter=adapter, worker_id="A", clock=clock, lease_sec=30
    )
    task = asyncio.create_task(worker_a.dispatch_once())
    await asyncio.wait_for(gate.started.wait(), timeout=2)
    assert outbox.rows[row_id].state == "SENDING" and outbox.rows[row_id].worker_id == "A"
    assert orders.orders[view.order_id].status is OrderStatus.SUBMITTED  # SENT 커밋됨
    return outbox, orders, view, row_id, gate, adapter, task


async def test_recovery_closed_row_rejects_stale_ack():
    clock = FixedClock()
    outbox, orders, view, row_id, gate, adapter, task = await _start_stale_worker(clock)

    # F2 복구: lease 만료 → 주문 UNKNOWN, 행 DONE(worker=recovery).
    clock.advance(31)
    assert outbox.rows[row_id].lease_until is not None
    assert outbox.rows[row_id].lease_until < clock.now
    recovered = orders.force(view.order_id, status=OrderStatus.UNKNOWN, unknown_since=clock.now)
    outbox.force(row_id, state="DONE", worker_id="recovery")

    gate.release.set()
    report = await task

    assert (report.conflicts, report.acknowledged, report.errors) == (1, 0, 0)
    final = orders.orders[view.order_id]
    assert final.status is OrderStatus.UNKNOWN and final.version == recovered.version
    assert final.exchange_order_id is None  # A의 ACK(ex-late)는 반영되지 않았다
    assert [e.event for e in orders.events] == ["SENT"]
    assert (outbox.rows[row_id].state, outbox.rows[row_id].worker_id) == ("DONE", "recovery")
    assert adapter.calls == [view.client_order_id]


async def test_order_version_conflict_rolls_back_outbox_fence_in_same_tx():
    """펜스는 통과했지만(행은 아직 A의 SENDING) 주문이 다른 경로로 바뀐 경우 —
    같은 tx이므로 `mark_done`까지 되돌아간다(행이 DONE으로 고아가 되지 않는다)."""
    clock = FixedClock()
    outbox, orders, view, row_id, gate, _, task = await _start_stale_worker(clock)

    moved = orders.force(view.order_id, status=OrderStatus.UNKNOWN, unknown_since=clock.now)
    gate.release.set()
    report = await task

    assert report.conflicts == 1 and report.acknowledged == 0
    row = outbox.rows[row_id]
    assert (row.state, row.worker_id) == ("SENDING", "A")  # 롤백 — F2 복구가 이어받는다
    final = orders.orders[view.order_id]
    assert (final.status, final.version) == (OrderStatus.UNKNOWN, moved.version)
    assert final.exchange_order_id is None


async def test_requeued_row_taken_by_other_worker_blocks_stale_worker():
    """lease 만료 행이 PENDING으로 되돌아가 워커 B가 처리한 뒤 A가 늦게 돌아오면
    A의 쓰기는 거부되고 B의 결과만 남는다(행당 최종 쓰기 정확히 1회)."""
    clock = FixedClock()
    outbox, orders, view, row_id, gate, adapter, task = await _start_stale_worker(clock)

    clock.advance(31)
    outbox.force(row_id, state="PENDING", worker_id=None, lease_until=None)
    adapter_b = ScriptedAdapter()  # 즉시 ACK
    worker_b = make_dispatcher(
        outbox=outbox, orders=orders, adapter=adapter_b, worker_id="B", clock=clock
    )
    # B는 SUBMITTED 주문을 보고 역조회를 시도한다(§5.4) — 없으면 재전송.
    report_b = await worker_b.dispatch_once()
    assert report_b.acknowledged == 1 and adapter_b.lookup_calls == [view.client_order_id]
    acked = orders.orders[view.order_id]
    assert acked.status is OrderStatus.ACKNOWLEDGED and acked.exchange_order_id != "ex-late"

    gate.release.set()
    report_a = await task
    assert (report_a.conflicts, report_a.acknowledged) == (1, 0)
    final = orders.orders[view.order_id]
    assert (final.version, final.exchange_order_id) == (acked.version, acked.exchange_order_id)
    assert (outbox.rows[row_id].state, outbox.rows[row_id].worker_id) == ("DONE", "B")
    assert [e.event for e in orders.events] == ["SENT", "ACK"]
    assert outbox.rows[row_id].updated_at >= clock.now - timedelta(seconds=0)
