"""L4-14 DoD — 디스패처 3워커 동시 실행, 행당 거래소 호출 정확히 1회.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §5.1(outbox claim SKIP
LOCKED), §9 L4-14 "3워커 정확히 1회".

포트 대역(`tests/support/oms_outbox_fakes.py`) 위에서 디스패처 계층의 보장을
증명한다: 클레임이 원자적이면 워커 수·배치 크기·인터리빙과 무관하게 각 outbox
행은 정확히 한 워커가 한 번만 전송하고 한 번만 닫는다. 실DB(`order_command_outbox`
+ `FOR UPDATE SKIP LOCKED`) 변형은 L4-06/08 이후 같은 케이스로 추가한다
(task-1538 note — 스키마·어댑터 부재).
"""
from __future__ import annotations

import asyncio
from collections import Counter
from uuid import UUID

from src.data.models.trading import Order, OrderStatus
from src.exchanges.common.error_taxonomy import SentUnknownError
from src.services.oms.application.outbox_dispatcher import DispatchReport, OutboxDispatcher
from tests.support.oms_outbox_fakes import (
    FixedClock,
    InMemoryOrderRepo,
    InMemoryOutboxRepo,
    ScriptedAdapter,
    enqueue,
    make_dispatcher,
    make_order_view,
)

WORKERS = ("w1", "w2", "w3")


def _yielding(times: int = 3, *, drop_every: int | None = None) -> ScriptedAdapter:
    """전송 중 이벤트 루프를 여러 번 양보해 워커 간 인터리빙을 강제한다."""
    counter = {"n": 0}

    async def hook(order: Order) -> Order:
        for _ in range(times):
            await asyncio.sleep(0)
        counter["n"] += 1
        if drop_every is not None and counter["n"] % drop_every == 0:
            raise SentUnknownError(venue="bitget")
        return order.model_copy(
            update={
                "exchange_order_id": f"ex-{order.client_order_id}",
                "status": OrderStatus.SUBMITTED,
            }
        )

    return ScriptedAdapter(on_place=hook)


async def _drain(
    dispatchers: list[OutboxDispatcher], outbox: InMemoryOutboxRepo, *, limit: int
) -> list[DispatchReport]:
    reports: list[DispatchReport] = []
    while any(r.state == "PENDING" for r in outbox.rows.values()):
        rounds = await asyncio.gather(*(d.dispatch_once(limit=limit) for d in dispatchers))
        reports.extend(rounds)
    return reports


Setup = tuple[InMemoryOutboxRepo, InMemoryOrderRepo, list[OutboxDispatcher], list[UUID]]


async def _setup(n: int, adapter: ScriptedAdapter) -> Setup:
    clock = FixedClock()
    outbox, orders = InMemoryOutboxRepo(clock=clock), InMemoryOrderRepo()
    order_ids = []
    for _ in range(n):
        view = orders.add(make_order_view())
        await enqueue(outbox, view)
        order_ids.append(view.order_id)
    dispatchers = [
        make_dispatcher(outbox=outbox, orders=orders, adapter=adapter, worker_id=w, clock=clock)
        for w in WORKERS
    ]
    return outbox, orders, dispatchers, order_ids


async def test_three_workers_send_each_row_exactly_once():
    adapter = _yielding()
    outbox, orders, dispatchers, order_ids = await _setup(50, adapter)

    reports = await _drain(dispatchers, outbox, limit=7)

    assert Counter(adapter.calls) == Counter(orders.orders[o].client_order_id for o in order_ids)
    assert all(c == 1 for c in Counter(adapter.calls).values()) and len(adapter.calls) == 50
    assert sum(r.acknowledged for r in reports) == 50
    assert sum(r.claimed for r in reports) == 50
    assert sum(r.conflicts + r.errors for r in reports) == 0
    assert {r.state for r in outbox.rows.values()} == {"DONE"}
    assert {r.worker_id for r in outbox.rows.values()} <= set(WORKERS)
    assert len({r.worker_id for r in outbox.rows.values()}) > 1  # 실제로 분산됐다
    assert all(orders.orders[o].status is OrderStatus.ACKNOWLEDGED for o in order_ids)
    assert all(orders.orders[o].version == 3 for o in order_ids)
    assert Counter(e.event for e in orders.events) == {"SENT": 50, "ACK": 50}


async def test_response_loss_under_concurrency_is_still_exactly_once():
    """일부 응답 유실(F3)이 섞여도 어느 행도 두 번 전송되지 않는다."""
    adapter = _yielding(drop_every=3)
    outbox, orders, dispatchers, order_ids = await _setup(30, adapter)

    reports = await _drain(dispatchers, outbox, limit=4)

    assert all(c == 1 for c in Counter(adapter.calls).values()) and len(adapter.calls) == 30
    statuses = Counter(orders.orders[o].status for o in order_ids)
    assert statuses == {OrderStatus.ACKNOWLEDGED: 20, OrderStatus.UNKNOWN: 10}
    assert sum(r.unknown for r in reports) == 10
    assert {r.state for r in outbox.rows.values()} == {"DONE"}  # UNKNOWN도 재전송 금지


async def test_worker_never_finalizes_a_row_it_did_not_claim():
    """negative — 다른 워커가 SENDING 중인 행은 클레임 대상이 아니다(SKIP LOCKED)."""
    adapter = _yielding(times=10)
    outbox, orders, dispatchers, _ = await _setup(3, adapter)
    d1, d2, _ = dispatchers

    first = asyncio.create_task(d1.dispatch_once(limit=3))
    await asyncio.sleep(0)  # d1이 클레임을 끝내고 전송 중
    assert {r.state for r in outbox.rows.values()} == {"SENDING"}
    second = await d2.dispatch_once(limit=3)
    assert second.claimed == 0
    report = await first
    assert report.acknowledged == 3 and len(adapter.calls) == 3
    assert {r.worker_id for r in outbox.rows.values()} == {"w1"}
