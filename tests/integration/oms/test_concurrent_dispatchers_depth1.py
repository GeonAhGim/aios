"""L4-14 depth1 — D2 하한 보강: 음성 3종, 실패 주입, 성능 단언, 게이트 적색 재현.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §5.1(outbox claim SKIP
LOCKED), §9 L4-14 "3워커 정확히 1회".

기존 테스트(test_concurrent_dispatchers.py, test_stale_worker_late_write.py,
test_outbox_dispatcher.py)가 포트 대역 위 3워커 레이스·늦은 쓰기 거부·단위 케이스를
증명했다. 이 파일은 D2 하한이 미달이던 항목만 보강한다:

- test_negative_claim_empty_queue: PENDING 행 0행일 때 claim은 0, 오류 없음
- test_negative_dispatch_after_all_done: 행이 이미 DONE 일 때 dispatch는 nothing
- test_negative_dead_before_claim: 행이 DEAD 일 때 claim 대상에서 제외됨
- test_failure_injection_adapter_crash: 어댑터 OSError → UNKNOWN, outbox DONE
- test_gate_red_broken_claim_duplicates: _RacyOutboxRepo로 claim 원자성 깨면
  중복 전송 관측 → 이 파일의 단언이 실제로 회귀를 잡는지 red-line 확인
- test_perf_*: PerfBudget.best_of_async 성능 단언 + perf_inject 가상 지연 10배 적색 재현
- test_negative_gate_denied_no_adapter_call: pre_send_gate 거부 → 어댑터 호출 0

모든 테스트는 포트 대역(InMemoryOutboxRepo/InMemoryOrderRepo) 위이며,
300줄 이하 유지.
"""

from __future__ import annotations

import asyncio
from collections import Counter

import pytest

from src.data.models.trading import OrderStatus
from tests.support.oms_outbox_fakes import (
    FixedClock,
    InMemoryOutboxRepo,
)
from tests.support.oms_outbox_fakes_orders import (
    InMemoryOrderRepo,
    ScriptedAdapter,
    deny_gate,
    enqueue,
    make_dispatcher,
    make_order_view,
)
from tests.support.perf_budget import PerfBudget
from tests.support.perf_inject import (
    reset_virtual_totals,
    virtual_delayed_async,
    virtual_totals,
)

WORKERS = ("w1", "w2", "w3")


# ---- 음성 (negative) 테스트 3종 --------------------------------------------------


async def test_negative_claim_empty_queue():
    """PENDING 행이 0행일 때 dispatch_once은 claimed=0, 오류 없이 종료."""
    clock = FixedClock()
    outbox = InMemoryOutboxRepo(clock=clock)
    orders = InMemoryOrderRepo()
    adapter = ScriptedAdapter()
    d = make_dispatcher(outbox=outbox, orders=orders, adapter=adapter, clock=clock)

    report = await d.dispatch_once(limit=10)
    assert (report.claimed, report.acknowledged, report.errors) == (0, 0, 0)
    assert adapter.calls == []


async def test_negative_dispatch_after_all_done():
    """행이 이미 DONE 상태일 때 dispatch_once은 해당 행을 재처리하지 않는다."""
    clock = FixedClock()
    outbox = InMemoryOutboxRepo(clock=clock)
    orders = InMemoryOrderRepo()
    view = orders.add(make_order_view())
    row_id = await enqueue(outbox, view)

    # 먼저 한 번 처리해 DONE으로 만듦
    adapter = ScriptedAdapter()
    d = make_dispatcher(outbox=outbox, orders=orders, adapter=adapter, clock=clock)
    first = await d.dispatch_once()
    assert first.claimed == 1
    assert outbox.rows[row_id].state == "DONE"

    # 두 번째 dispatch는 다시 처리하지 않음
    second = await d.dispatch_once()
    assert (second.claimed, second.acknowledged) == (0, 0)
    assert adapter.calls == [view.client_order_id]  # 호출 증가 없음


async def test_negative_dead_before_claim():
    """행이 DEAD 상태일 때 claim_batch는 해당 행을 건너뛴다."""
    clock = FixedClock()
    outbox = InMemoryOutboxRepo(clock=clock)
    orders = InMemoryOrderRepo()
    view = orders.add(make_order_view())
    row_id = await enqueue(outbox, view)
    outbox.force(row_id, state="DEAD")

    adapter = ScriptedAdapter()
    d = make_dispatcher(outbox=outbox, orders=orders, adapter=adapter, clock=clock)
    report = await d.dispatch_once()
    assert report.claimed == 0
    assert adapter.calls == []
    assert outbox.rows[row_id].state == "DEAD"


# ---- 실패 주입 테스트 -----------------------------------------------------------


async def test_failure_injection_adapter_crash():
    """어댑터가 OSError를 던지면: order → UNKNOWN, outbox → DONE, 재전송 없음."""
    clock = FixedClock()
    outbox = InMemoryOutboxRepo(clock=clock)
    orders = InMemoryOrderRepo()
    view = orders.add(make_order_view())
    row_id = await enqueue(outbox, view)

    async def crash(order):
        raise OSError("connection reset")

    adapter = ScriptedAdapter(on_place=crash)
    d = make_dispatcher(outbox=outbox, orders=orders, adapter=adapter, clock=clock)

    report = await d.dispatch_once()
    assert report.unknown == 1
    assert report.retried == 0
    assert outbox.rows[row_id].state == "DONE"
    final = orders.orders[view.order_id]
    assert final.status is OrderStatus.UNKNOWN
    assert final.unknown_since == clock.now


# ---- 게이트 적색 재현: broken claim atomicity ------------------------------------


class _RacyOutboxRepo(InMemoryOutboxRepo):
    """claim_batch의 후보 스냅샷과 SENDING 기록 사이에 await를 끼워 원자성을 깬다."""

    async def claim_batch(self, conn, *, worker_id, limit, lease_sec):
        now = self._clock()
        candidates = [r for r in self.rows.values() if r.state == "PENDING"][:limit]
        await asyncio.sleep(0)
        claimed = []
        for row in candidates:
            new = row.model_copy(
                update={"state": "SENDING", "worker_id": worker_id, "updated_at": now}
            )
            self._put(conn, new)
            claimed.append(new)
        return claimed


async def test_gate_red_broken_claim_duplicates():
    """claim 원자성을 깨면 중복 전송이 관측된다 -- 정확히-1회 단언이 적색이 된다."""
    clock = FixedClock()
    outbox = _RacyOutboxRepo(clock=clock)
    orders = InMemoryOrderRepo()
    for _ in range(5):
        await enqueue(outbox, orders.add(make_order_view()))

    async def slow_place(order):
        for _ in range(5):
            await asyncio.sleep(0)
        return order.model_copy(update={"exchange_order_id": "ex", "status": OrderStatus.SUBMITTED})

    adapter = ScriptedAdapter(on_place=slow_place)
    dispatchers = [
        make_dispatcher(outbox=outbox, orders=orders, adapter=adapter, worker_id=w, clock=clock)
        for w in WORKERS
    ]

    reports = await asyncio.gather(*(d.dispatch_once(limit=10) for d in dispatchers))
    counts = Counter(adapter.calls)
    conflicts = sum(r.conflicts for r in reports)
    assert any(c > 1 for c in counts.values()) or conflicts > 0, "회귀 미관측 — 게이트 무력"
    with pytest.raises(AssertionError):  # 정상 불변 단언이 이 입력에서 적색
        assert all(c == 1 for c in counts.values()) and conflicts == 0


# ---- 성능 단언 테스트 -------------------------------------------------------------


# 실측(best_of_async n=5, 5행 dispatch_once, 포트 대역, 2026-11-14 로컬): wall 0.40~0.58ms
# (cpu는 Windows 15.6ms 양자화로 0.0 -> wall 기준). 예산 20ms = 실측 최대의 약 35배 여유.
_DISPATCH_BUDGET_MS = 20.0


async def _dispatch_five(adapter_delay_s: float) -> int:
    clock = FixedClock()
    outbox = InMemoryOutboxRepo(clock=clock)
    orders = InMemoryOrderRepo()
    for _ in range(5):
        await enqueue(outbox, orders.add(make_order_view()))
    adapter = ScriptedAdapter()
    if adapter_delay_s:
        adapter.place_order = virtual_delayed_async(adapter.place_order, adapter_delay_s)
    d = make_dispatcher(outbox=outbox, orders=orders, adapter=adapter, clock=clock)
    return (await d.dispatch_once(limit=10)).acknowledged


@pytest.mark.perf
async def test_perf_dispatch_five_rows_within_budget():
    reset_virtual_totals()
    sample = await PerfBudget().best_of_async(lambda: _dispatch_five(0.0), n=5, warmup=1)
    assert sample.result == 5
    assert sample.wall_ms < _DISPATCH_BUDGET_MS, sample


@pytest.mark.perf
async def test_perf_gate_red_10x_virtual_delay_breaks_budget():
    """어댑터 호출당 가상 지연을 예산 규모(10배)로 주입하면 같은 예산 단언이 적색이 된다."""
    reset_virtual_totals()
    delay_s = _DISPATCH_BUDGET_MS * 10 / 5 / 1000  # 5행 합계가 예산의 10배
    sample = await PerfBudget().best_of_async(lambda: _dispatch_five(delay_s), n=5, warmup=1)
    assert virtual_totals()[1] > 0
    with pytest.raises(AssertionError):
        assert sample.wall_ms < _DISPATCH_BUDGET_MS


# ---- 게이트 거부 음성 테스트 ------------------------------------------------------


async def test_negative_gate_denied_no_adapter_call():
    """pre_send_gate가 거부하면 어댑터가 호출되지 않는다 (I-10)."""
    clock = FixedClock()
    outbox = InMemoryOutboxRepo(clock=clock)
    orders = InMemoryOrderRepo()
    view = orders.add(make_order_view())
    row_id = await enqueue(outbox, view)

    adapter = ScriptedAdapter()
    d = make_dispatcher(outbox=outbox, orders=orders, adapter=adapter, clock=clock, gate=deny_gate)

    report = await d.dispatch_once()
    assert report.gate_denied == 1
    assert adapter.calls == []
    assert outbox.rows[row_id].state == "DEAD"  # 거부 -> DEAD, 재시도 없음
