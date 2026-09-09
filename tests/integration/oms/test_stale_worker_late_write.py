"""L4-14 DoD — 리스를 잃은(stale) 워커의 늦은 쓰기는 거부된다.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §5.1 "outbox done/retry/dead
`WHERE id=$1 AND state='SENDING' AND worker_id=$2` RETURNING(리스 잃은 워커의 늦은
쓰기 차단)", §6 F2(lease 만료 → 복구가 UNKNOWN 처리), 105번 §2.

시나리오: 워커 A가 행을 SENDING으로 선점하고 거래소 호출이 길어진다. 그 사이
복구 워커(F2)가 lease 만료를 보고 주문을 UNKNOWN, 행을 DONE으로 닫는다. A의
응답이 뒤늦게 도착해도 (1) outbox 펜스가 0행 → `ConcurrencyConflictError`,
(2) 주문 전이는 같은 tx라 실행되지 않는다. 포트 대역 위 증명이며 실DB 변형은
L4-06/08 이후 추가(task-1538 note).

DEPTH_L4_BR(task-2722)가 원 리프(task-1567, 46ace35)를 D1로 판정 — 실DB
"늦은 쓰기 거부" 증명은 강하지만 수치 성능/지연 단언(D2)과 게이트가 실제로
회귀를 잡는지 보이는 명시적 CI red-line 테스트가 없었다. task-2766 DEEPEN으로
아래 두 가지를 추가한다:
- `test_stale_write_rejection_round_trip_budget_real_db` — 절대 벽시계
  시간은 환경(DB/CPU) 편차에 노출되므로 print(비차단, task-1038/1521
  decision과 동일 관례), CI 차단 게이트는 거부 경로의 순차 DB 왕복 수
  정확 합계(재시도·추가 조회 없이 즉시 실패해야 한다는 것을 증명).
- `test_broken_fence_lets_stale_write_corrupt_recovered_order_real_db` —
  `_UnfencedOutboxRepository`로 `mark_done`의 펜스(`AND worker_id=$expected`)만
  제거해(주문 쪽은 건드리지 않아 낙관적 잠금과 이중으로 겹치지 않게 함) 이
  펜스가 없으면 리스를 잃은 워커의 늦은 ACK가 실제로 outbox가 이미 다른
  워커에게 넘어간 주문을 조용히 덮어쓴다는 것을 증명한다 — 이 파일의
  "거부됨" 단언들이 장식이 아니라 회귀를 실제로 적색으로 만드는 게이트임을
  보인다.
"""
from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime, timedelta, timezone
from uuid import UUID

import asyncpg

from src.data.models.trading import Order, OrderStatus
from src.services.oms.adapters.order_repository import PostgresOrderRepository
from src.services.oms.adapters.outbox_repository import OutboxRepository
from src.services.oms.application.outbox_dispatcher import OutboxDispatcher
from tests.integration.oms.conftest import create_test_user, insert_order
from tests.performance.oms._fixtures import drain_outbox_backlog
from tests.performance.oms.conftest import attach_round_trip_logger
from tests.support.oms_outbox_fakes import (
    FixedClock,
    InMemoryOrderRepo,
    InMemoryOutboxRepo,
    ScriptedAdapter,
    allow_gate,
    enqueue,
    make_dispatcher,
    make_order_view,
    submit_payload,
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


# ---- 실DB(task-1567 L4-14b) ---------------------------------------------------
# `test_recovery_closed_row_rejects_stale_ack`(포트 대역)와 같은 시나리오를
# 실제 `order_command_outbox`/`orders`에 대고 반복한다 — 펜스(`mark_done`의
# `WHERE state='SENDING' AND worker_id=$expected`)가 실SQL에서 0행을 반환해
# `ConcurrencyConflictError`를 던지고, 같은 tx의 주문 전이(`_finalize_submit`의
# `w.transition(...)`)가 실행 자체를 못 해 A의 ACK가 반영되지 않음을 증명한다.
class _RealGate:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def place(self, order: Order) -> Order:
        self.started.set()
        await self.release.wait()
        return order.model_copy(
            update={"exchange_order_id": "ex-late", "status": OrderStatus.SUBMITTED}
        )


async def test_recovery_closed_row_rejects_stale_ack_real_db(pool):
    order_repo, outbox_repo = PostgresOrderRepository(), OutboxRepository()
    user_id = await create_test_user(pool)
    async with pool.acquire() as conn:
        order_id = await insert_order(conn, user_id, status="VALIDATED")
        view = await order_repo.get_for_update(conn, order_id)
        row_id = await outbox_repo.enqueue(
            conn, order_id=order_id, command_type="SUBMIT", payload=submit_payload(view),
            not_before=datetime.now(timezone.utc),
        )

    gate = _RealGate()
    adapter = ScriptedAdapter(on_place=gate.place)

    async def resolve(tenant_id: UUID, exchange: str) -> ScriptedAdapter:
        return adapter

    worker_a = OutboxDispatcher(
        pool, outbox_repo=outbox_repo, order_repo=order_repo, resolve_adapter=resolve,
        pre_send_gate=allow_gate, worker_id="A", lease_sec=30,
    )
    task = asyncio.create_task(worker_a.dispatch_once())
    await asyncio.wait_for(gate.started.wait(), timeout=5)

    outbox_row = await pool.fetchrow(
        "SELECT state, worker_id FROM order_command_outbox WHERE id = $1", row_id
    )
    order_row = await pool.fetchrow(
        "SELECT status, sent_at FROM orders WHERE order_id = $1", order_id
    )
    assert (outbox_row["state"], outbox_row["worker_id"]) == ("SENDING", "A")
    assert order_row["status"] == "SUBMITTED" and order_row["sent_at"] is not None

    # F2 복구(이 리프 밖)가 lease 만료를 보고 이미 처리한 상태를 흉내낸다 —
    # outbox 펜스는 워커 식별자로만 막으므로 lease_until 경과를 기다릴 필요 없다.
    await pool.execute(
        "UPDATE orders SET status = 'UNKNOWN', unknown_since = now() WHERE order_id = $1",
        order_id,
    )
    await pool.execute(
        "UPDATE order_command_outbox SET state = 'DONE', worker_id = 'recovery' WHERE id = $1",
        row_id,
    )

    gate.release.set()
    report = await task

    assert (report.conflicts, report.acknowledged, report.errors) == (1, 0, 0)
    final_order = await pool.fetchrow(
        "SELECT status, exchange_order_id FROM orders WHERE order_id = $1", order_id
    )
    final_outbox = await pool.fetchrow(
        "SELECT state, worker_id FROM order_command_outbox WHERE id = $1", row_id
    )
    assert final_order["status"] == "UNKNOWN"
    assert final_order["exchange_order_id"] is None  # A의 ACK(ex-late)는 반영되지 않았다
    assert (final_outbox["state"], final_outbox["worker_id"]) == ("DONE", "recovery")


def _asyncpg_dsn() -> str:
    return os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")


async def test_stale_write_rejection_round_trip_budget_real_db() -> None:
    """DEPTH_L4_BR(task-2722) D2 — 실DB 늦은 쓰기 거부 경로의 수치 성능 단언.

    절대 벽시계 시간은 환경(DB/CPU) 편차에 노출되므로 print(비차단 —
    tests/performance/oms/test_outbox_dispatch_latency.py, task-1038/1521
    decision과 동일 관례) — CI 차단 게이트는 순차 DB 왕복 수의 정확 합계로
    건다. `mark_done`이 0행을 반환해 `ConcurrencyConflictError`를 던지는 이
    경로는 재시도나 추가 조회 없이 즉시 실패해야 한다: BEGIN(1) + 펜스
    UPDATE(0행, 1) + ROLLBACK(1) + 연결 반환 시 리셋(1) = 4(실측,
    `attach_round_trip_logger`로 확인). 이 상한을 벗어나면 거부 경로에
    불필요한 왕복(재조회·재시도 루프)이 섞여든 회귀다. 단일 물리 커넥션
    (`max_size=1`)이 필요해 이 파일 공용 `pool`(max_size=4) 픽스처가 아닌
    전용 풀을 쓴다(tests/performance/oms/conftest.py와 동일 이유).
    """
    pool = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=1)
    try:
        await drain_outbox_backlog(pool)  # 전역 큐 — 잔여 행이 왕복 수 예산을 어지럽힌다
        order_repo, outbox_repo = PostgresOrderRepository(), OutboxRepository()
        user_id = await create_test_user(pool)
        async with pool.acquire() as conn:
            order_id = await insert_order(conn, user_id, status="VALIDATED")
            view = await order_repo.get_for_update(conn, order_id)
            row_id = await outbox_repo.enqueue(
                conn, order_id=order_id, command_type="SUBMIT", payload=submit_payload(view),
                not_before=datetime.now(timezone.utc),
            )

        gate = _RealGate()
        adapter = ScriptedAdapter(on_place=gate.place)

        async def resolve(tenant_id: UUID, exchange: str) -> ScriptedAdapter:
            return adapter

        worker_a = OutboxDispatcher(
            pool, outbox_repo=outbox_repo, order_repo=order_repo, resolve_adapter=resolve,
            pre_send_gate=allow_gate, worker_id="A", lease_sec=30,
        )
        task = asyncio.create_task(worker_a.dispatch_once())
        await asyncio.wait_for(gate.started.wait(), timeout=5)

        # F2 복구가 lease 만료를 보고 이미 처리한 상태를 흉내낸다(위 테스트와 동일).
        await pool.execute(
            "UPDATE orders SET status = 'UNKNOWN', unknown_since = now() WHERE order_id = $1",
            order_id,
        )
        await pool.execute(
            "UPDATE order_command_outbox SET state = 'DONE', worker_id = 'recovery' WHERE id = $1",
            row_id,
        )

        queries = await attach_round_trip_logger(pool)
        queries.clear()  # 로거 부착/연결 반환 자체의 왕복은 측정에서 뺀다
        started = time.monotonic()
        gate.release.set()
        report = await task
        elapsed_ms = (time.monotonic() - started) * 1000.0

        assert (report.conflicts, report.acknowledged, report.errors) == (1, 0, 0)
        print(
            f"\nstale-write rejection(real db): elapsed={elapsed_ms:.2f}ms(비차단, "
            f"환경 편차) round_trips={len(queries)}(budget==4, CI 차단)"
        )
        assert len(queries) == 4, (
            f"늦은 쓰기 거부 경로의 DB 왕복 수({len(queries)})가 예산(4)과 다릅니다 — "
            "거부 경로가 재시도나 추가 조회를 하고 있을 수 있습니다(구조 변경, 리뷰 필요)."
        )
    finally:
        await pool.close()


class _UnfencedOutboxRepository(OutboxRepository):
    """negative 전용(CI red-line, task-2766) — `mark_done`의 펜스(`AND
    worker_id=$expected_worker`)만 제거하고 나머지는 그대로 둔다. 이 펜스가
    없으면 리스를 잃은 워커의 늦은 ACK가 outbox 쪽 재할당을 무시하고 그대로
    커밋된다는 것을 증명하려는 목적이라, 이 테스트는 주문 쪽 낙관적 잠금
    (`orders.version`)이 함께 걸리지 않도록 주문 자체는 건드리지 않는다 —
    건드리면 이중 방어(105번 §2)라 펜스 제거만의 효과가 가려진다."""

    async def mark_done(
        self, conn: asyncpg.Connection, id: UUID, *, expected_worker: str
    ) -> None:
        row = await conn.fetchrow(
            "UPDATE order_command_outbox SET state = 'DONE', updated_at = now() "
            "WHERE id = $1 "  # 의도적으로 state='SENDING'/worker_id=$2 재확인 생략
            "RETURNING id",
            id,
        )
        assert row is not None  # id가 존재하지 않으면 테스트 설정 자체가 잘못된 것


async def test_broken_fence_lets_stale_write_corrupt_recovered_order_real_db(
    pool: asyncpg.Pool,
) -> None:
    """DEPTH_L4_BR(task-2722) — 실DB 명시적 CI red-line 회귀 테스트.

    `_UnfencedOutboxRepository`로 outbox 펜스만 제거하고, outbox 행만 다른
    워커(`recovery`)에게 재할당된 것으로 흉내낸다(주문은 그대로 SUBMITTED —
    위 `_UnfencedOutboxRepository` docstring 참조). 정상 코드(펜스 있음)라면
    `mark_done`이 0행을 반환해 `ConcurrencyConflictError`로 막히지만, 펜스를
    제거하면 A의 늦은 ACK가 그대로 커밋되어 이미 다른 워커에게 넘어간 outbox
    행 아래에서 주문이 조용히 ACKNOWLEDGED로 확정된다 — 정상 코드에서는 절대
    관측되지 않는 증상이다. 이 증상이 실제로 나타남을 확인해,
    `test_recovery_closed_row_rejects_stale_ack_real_db`류 단언이 장식이
    아니라 펜스 제거를 실제로 적색으로 만드는 게이트임을 증명한다.
    """
    await drain_outbox_backlog(pool)  # 전역 큐 — 잔여 행이 gate.started를 가로챈다
    order_repo, outbox_repo = PostgresOrderRepository(), _UnfencedOutboxRepository()
    user_id = await create_test_user(pool)
    async with pool.acquire() as conn:
        order_id = await insert_order(conn, user_id, status="VALIDATED")
        view = await order_repo.get_for_update(conn, order_id)
        row_id = await outbox_repo.enqueue(
            conn, order_id=order_id, command_type="SUBMIT", payload=submit_payload(view),
            not_before=datetime.now(timezone.utc),
        )

    gate = _RealGate()
    adapter = ScriptedAdapter(on_place=gate.place)

    async def resolve(tenant_id: UUID, exchange: str) -> ScriptedAdapter:
        return adapter

    worker_a = OutboxDispatcher(
        pool, outbox_repo=outbox_repo, order_repo=order_repo, resolve_adapter=resolve,
        pre_send_gate=allow_gate, worker_id="A", lease_sec=30,
    )
    task = asyncio.create_task(worker_a.dispatch_once())
    await asyncio.wait_for(gate.started.wait(), timeout=5)

    # outbox만 다른 워커에게 재할당된 것으로 흉내(주문은 건드리지 않는다).
    await pool.execute(
        "UPDATE order_command_outbox SET state = 'DONE', worker_id = 'recovery' WHERE id = $1",
        row_id,
    )

    gate.release.set()
    report = await task

    final_order = await pool.fetchrow(
        "SELECT status, exchange_order_id FROM orders WHERE order_id = $1", order_id
    )
    assert (report.conflicts, report.acknowledged) == (0, 1), (
        "펜스를 제거했는데도 늦은 쓰기가 거부됐다 — 이 red-line이 실제로 펜스의 "
        "부재를 잡아내지 못하는 무력한 테스트일 수 있다."
    )
    assert final_order["status"] == "ACKNOWLEDGED" and final_order["exchange_order_id"] == (
        "ex-late"
    ), (
        "펜스 제거의 증상(outbox가 이미 다른 워커에게 넘어간 행 아래에서 주문이 "
        "늦은 ACK로 조용히 확정됨)이 관측되지 않았다."
    )
