"""L4-14 DoD — 디스패처 3워커 동시 실행, 행당 거래소 호출 정확히 1회.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §5.1(outbox claim SKIP
LOCKED), §9 L4-14 "3워커 정확히 1회".

포트 대역(`tests/support/oms_outbox_fakes.py`) 위에서 디스패처 계층의 보장을
증명한다: 클레임이 원자적이면 워커 수·배치 크기·인터리빙과 무관하게 각 outbox
행은 정확히 한 워커가 한 번만 전송하고 한 번만 닫는다. 실DB(`order_command_outbox`
+ `FOR UPDATE SKIP LOCKED`) 변형은 L4-06/08 이후 같은 케이스로 추가한다
(task-1538 note — 스키마·어댑터 부재).

DEPTH_L4_BR(task-2722)가 원 리프(task-1538, 38e811f)를 D1로 판정 — 3워커
레이스 증명은 강하지만 수치 성능/지연 단언(D2)과 게이트가 실제로 회귀를
잡는지 보이는 명시적 CI red-line 테스트가 없었다. task-2759 DEEPEN으로
아래 두 가지를 추가한다:
- `test_three_workers_throughput_has_bounded_wall_clock_latency` — 수치
  지연/처리량 단언(포트 대역은 순수 asyncio 스케줄링이라 실DB 성능테스트
  (`tests/performance/oms/test_outbox_dispatch_latency.py`, task-2323)와
  달리 절대시간 상한을 비차단 print가 아니라 CI 차단 게이트로 써도 환경
  편차에 노출되지 않는다).
- `test_broken_claim_atomicity_is_caught_by_exactly_once_gate` — `_RacyOutboxRepo`로
  claim_batch의 원자성(await 없음)을 의도적으로 깨서, 이 회귀가 실제로
  "행당 정확히 1회" 불변을 위반하는 관측 가능한 증상(중복 전송 또는 펜스
  충돌)을 낸다는 것을 증명한다 — 위 테스트들의 단언이 장식이 아니라 실제
  회귀를 잡는 게이트임을 보이는 red-line.

DEPTH_L4_BR(task-2722)가 원 리프(task-1567, 46ace35 — 이 파일 아래쪽 실DB
섹션)를 D1로 판정 — 실DB 3워커 레이스 증명은 강하지만 이 실DB 변형에는 위와
같은 명시적 CI red-line이 없었다. task-2766 DEEPEN으로
`test_broken_claim_atomicity_is_caught_by_exactly_once_gate_real_db`를
추가한다 — 위 `_RacyOutboxRepo`(포트 대역)와 같은 취지를
`order_command_outbox`의 실제 SQL(`_CLAIM_SQL`)에 대고 재생한다: SKIP LOCKED
서브쿼리+UPDATE가 한 문장인 원자성을 SELECT 후보 조회와 상태 재확인 없는
UPDATE로 쪼개서, 실DB에서도 이 분리가 중복 전송을 낸다는 것을 증명한다.
"""
from __future__ import annotations

import asyncio
import json
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from uuid import UUID

import asyncpg

from src.data.models.trading import Order, OrderStatus
from src.exchanges.common.error_taxonomy import SentUnknownError
from src.services.oms.adapters.order_repository import PostgresOrderRepository
from src.services.oms.adapters.outbox_repository import OutboxRepository
from src.services.oms.application.outbox_dispatcher import DispatchReport, OutboxDispatcher
from src.services.oms.ports.repository import OutboxRow
from tests.integration.oms.conftest import create_test_user, insert_order
from tests.support.oms_outbox_fakes import (
    FakeConn,
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


async def _setup(
    n: int, adapter: ScriptedAdapter, *, outbox_cls: type[InMemoryOutboxRepo] = InMemoryOutboxRepo
) -> Setup:
    clock = FixedClock()
    outbox, orders = outbox_cls(clock=clock), InMemoryOrderRepo()
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


async def test_three_workers_throughput_has_bounded_wall_clock_latency():
    """DEPTH_L4_BR(task-2722) D2 — 수치 성능/처리량 단언(CI 차단 게이트).

    포트 대역은 실DB I/O 없이 순수 asyncio 스케줄링만 돌리므로(모듈 docstring
    "await 없음 — SKIP LOCKED 원자성 모델"), `tests/performance/oms/`의 실DB
    성능테스트(task-2323, task-1038/1521 decision — 절대시간은 print 비차단)와
    달리 절대 벽시계 시간 상한을 CI 차단 단언으로 써도 환경(CPU/DB) 편차에
    노출되지 않는다. 200건×3워커 처리가 이 상한을 넘으면 디스패치 루프에
    직렬화·교착·불필요한 폴링 지연이 섞여든 구조적 회귀다.
    """
    adapter = _yielding()
    outbox, orders, dispatchers, order_ids = await _setup(200, adapter)

    started = time.monotonic()
    reports = await _drain(dispatchers, outbox, limit=10)
    elapsed = time.monotonic() - started

    assert sum(r.acknowledged for r in reports) == 200
    assert sum(r.conflicts + r.errors for r in reports) == 0
    throughput = 200 / elapsed if elapsed > 0 else float("inf")
    print(
        f"\n3-worker in-memory outbox dispatch: n=200 elapsed={elapsed:.3f}s "
        f"throughput={throughput:.1f} rows/s (budget<5.0s, CI 차단)"
    )
    assert elapsed < 5.0, (
        f"3워커 200건 outbox 디스패치가 {elapsed:.3f}s로 상한(5.0s)을 초과했다 — "
        "디스패치 루프 성능 회귀입니다."
    )


class _RacyOutboxRepo(InMemoryOutboxRepo):
    """negative 전용(CI red-line, task-2759) — `claim_batch`의 원자성을 의도적으로
    깨서(후보 스냅샷과 SENDING 기록 사이에 `await`를 끼운다) 이 파일의 "행당
    정확히 1회" 단언들이 실제로 그 회귀를 잡아내는 게이트임을 증명한다. 정상
    `InMemoryOutboxRepo.claim_batch`는 await 없이 한 번에 끝나 두 워커가 같은
    PENDING 스냅샷을 동시에 보는 일이 있을 수 없다(모듈 docstring) — 여기서는
    그 보장을 제거한다."""

    async def claim_batch(
        self, conn: FakeConn, *, worker_id: str, limit: int, lease_sec: int
    ) -> list[OutboxRow]:
        self.claim_calls += 1
        now = self._clock()
        candidates = sorted(
            (r for r in self.rows.values() if r.state == "PENDING" and r.not_before <= now),
            key=lambda r: r.created_at,
        )[:limit]
        await asyncio.sleep(0)  # 회귀 주입 — 다른 워커의 claim_batch가 같은 스냅샷을 본다
        claimed: list[OutboxRow] = []
        for row in candidates:
            new = row.model_copy(
                update={
                    "state": "SENDING", "worker_id": worker_id,
                    "lease_until": now + timedelta(seconds=lease_sec), "updated_at": now,
                }
            )
            self._put(conn, new)
            claimed.append(new)
        return claimed


async def test_broken_claim_atomicity_is_caught_by_exactly_once_gate():
    """DEPTH_L4_BR(task-2722) — 명시적 CI red-line 회귀 테스트.

    `_RacyOutboxRepo`로 claim_batch의 원자성만 제거하고 나머지는 그대로 둔 채
    3워커를 돌린다. 원자성이 깨지면 두 워커가 같은 행을 동시에 SENDING으로
    "클레임"할 수 있고, 그중 하나는 (a) 어댑터를 중복 호출하거나 (b) 나중에
    outbox 펜스(`mark_done`의 `state='SENDING' AND worker_id=expected`)에서
    `ConcurrencyConflictError`로 걸린다 — 즉 정상 코드에서는 절대 발생하지
    않는 관측 가능한 증상이 남는다. 이 테스트는 그 증상이 실제로 나타남을
    확인해, `test_three_workers_send_each_row_exactly_once`류 단언이 장식이
    아니라 회귀를 실제로 적색으로 만드는 게이트임을 증명한다.
    """
    adapter = _yielding(times=5)
    outbox, orders, dispatchers, order_ids = await _setup(30, adapter, outbox_cls=_RacyOutboxRepo)

    reports = await _drain(dispatchers, outbox, limit=5)

    duplicate_calls = len(adapter.calls) - len(set(adapter.calls))
    conflicts = sum(r.conflicts for r in reports)
    assert duplicate_calls > 0 or conflicts > 0, (
        "claim_batch 원자성을 깼는데도 중복 전송이나 펜스 충돌이 하나도 관측되지 "
        "않았다 — 이 파일의 정확히-1회 단언들이 회귀를 잡지 못하는 무력한 게이트일 "
        "수 있다(red-line 실패)."
    )


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


# ---- 실DB(task-1567 L4-14b) ---------------------------------------------------
# 위 테스트들은 포트 대역(§5.1 SQL 의미론의 모델) 위에서 증명한다. 여기서는
# 같은 "3워커 정확히 1회" 케이스를 실제 `order_command_outbox`/`orders`
# (073beca589d5 L4-06 + OutboxRepository/PostgresOrderRepository L4-08)에
# 대고 반복해 SKIP LOCKED·조건부 UPDATE 배선 자체를 증명한다(모듈 docstring
# 예고 그대로). 워커 간 분배 비율은 스케줄링에 달려 있어 단언하지 않는다
# (tests/integration/oms/test_outbox_repository.py와 동일 관례) — "정확히
# 1회"의 구조적 보장(중복 전송 0, 전부 DONE)만 확인한다.
async def _setup_real_orders(
    pool, n: int
) -> tuple[PostgresOrderRepository, OutboxRepository, list[UUID], set[str]]:
    order_repo, outbox_repo = PostgresOrderRepository(), OutboxRepository()
    user_id = await create_test_user(pool)
    order_ids: list[UUID] = []
    client_order_ids: set[str] = set()
    async with pool.acquire() as conn:
        for _ in range(n):
            order_id = await insert_order(conn, user_id, status="VALIDATED")
            view = await order_repo.get_for_update(conn, order_id)
            await outbox_repo.enqueue(
                conn,
                order_id=order_id,
                command_type="SUBMIT",
                payload=submit_payload(view),
                not_before=datetime.now(timezone.utc),
            )
            order_ids.append(order_id)
            client_order_ids.add(view.client_order_id)
    return order_repo, outbox_repo, order_ids, client_order_ids


async def _drain_real(dispatchers: list[OutboxDispatcher], pool, order_ids: list[UUID]) -> None:
    for _ in range(20):
        remaining = await pool.fetchval(
            "SELECT count(*) FROM order_command_outbox "
            "WHERE order_id = ANY($1::uuid[]) AND state = 'PENDING'",
            order_ids,
        )
        if remaining == 0:
            return
        await asyncio.gather(*(d.dispatch_once(limit=5) for d in dispatchers))
    raise AssertionError("실DB 드레인이 라운드 상한 안에 끝나지 않았다")


async def test_three_workers_send_each_row_exactly_once_real_db(pool):
    order_repo, outbox_repo, order_ids, client_order_ids = await _setup_real_orders(pool, 15)
    adapter = ScriptedAdapter()

    async def resolve(tenant_id: UUID, exchange: str) -> ScriptedAdapter:
        return adapter

    dispatchers = [
        OutboxDispatcher(
            pool, outbox_repo=outbox_repo, order_repo=order_repo, resolve_adapter=resolve,
            pre_send_gate=allow_gate, worker_id=w,
        )
        for w in WORKERS
    ]

    await _drain_real(dispatchers, pool, order_ids)

    outbox_rows = await pool.fetch(
        "SELECT state, worker_id FROM order_command_outbox WHERE order_id = ANY($1::uuid[])",
        order_ids,
    )
    order_rows = await pool.fetch(
        "SELECT status, version, exchange_order_id FROM orders WHERE order_id = ANY($1::uuid[])",
        order_ids,
    )
    # claim_batch는 전역 큐라(설계상 order_id로 필터링하지 않는다) 공유
    # TEST_DATABASE_URL에 다른 테스트가 남겨둔(디스패처 없이 enqueue만 한)
    # PENDING SUBMIT 행도 같은 라운드에 같이 클레임될 수 있다 — 어댑터 호출
    # 집계는 이 테스트가 만든 client_order_id로만 좁혀서 본다.
    call_counts = Counter(c for c in adapter.calls if c in client_order_ids)
    assert set(call_counts) == client_order_ids  # 전부 최소 1회
    assert set(call_counts.values()) == {1}  # 행당 어댑터 호출 정확히 1회
    assert {r["state"] for r in outbox_rows} == {"DONE"}
    assert {r["worker_id"] for r in outbox_rows} <= set(WORKERS)
    assert {r["status"] for r in order_rows} == {"ACKNOWLEDGED"}
    assert {r["version"] for r in order_rows} == {2}  # VALIDATED->SUBMITTED->ACKNOWLEDGED
    assert all(r["exchange_order_id"] is not None for r in order_rows)


class _RacyOutboxRepository(OutboxRepository):
    """negative 전용(CI red-line, task-2766) — 실 `_CLAIM_SQL`(SKIP LOCKED
    서브쿼리 + UPDATE가 한 문장)의 원자성을 의도적으로 깨서, 이 회귀가 실DB에서도
    관측 가능한 증상(중복 전송)을 낸다는 것을 증명한다. 후보 조회(SELECT, 잠금
    없음)와 클레임(UPDATE, `state='PENDING'` 재확인 없음)을 분리한다 — 다른
    워커가 같은 후보를 동시에 봐도 UPDATE가 막지 않는다."""

    async def claim_batch(
        self, conn: asyncpg.Connection, *, worker_id: str, limit: int, lease_sec: int
    ) -> list[OutboxRow]:
        candidates = await conn.fetch(
            "SELECT id FROM order_command_outbox WHERE state = 'PENDING' AND not_before <= now() "
            "ORDER BY created_at LIMIT $1",
            limit,
        )
        ids = [r["id"] for r in candidates]
        if not ids:
            return []
        await asyncio.sleep(0.05)  # 회귀 주입 — 다른 워커의 SELECT가 같은 후보를 본다
        records = await conn.fetch(
            "UPDATE order_command_outbox SET state = 'SENDING', worker_id = $1, "
            "lease_until = now() + make_interval(secs => $2::double precision), "
            "updated_at = now() "
            "WHERE id = ANY($3::uuid[]) "  # 의도적으로 state = 'PENDING' 재확인 생략
            "RETURNING *",
            worker_id,
            lease_sec,
            ids,
        )
        return [
            OutboxRow(
                id=r["id"],
                order_id=r["order_id"],
                command_type=r["command_type"],
                payload=json.loads(r["payload"]) if isinstance(r["payload"], str) else r["payload"],
                state=r["state"],
                attempt=r["attempt"],
                not_before=r["not_before"],
                lease_until=r["lease_until"],
                worker_id=r["worker_id"],
                last_error=r["last_error"],
                created_at=r["created_at"],
                updated_at=r["updated_at"],
            )
            for r in records
        ]


async def test_broken_claim_atomicity_is_caught_by_exactly_once_gate_real_db(pool):
    """DEPTH_L4_BR(task-2722) — 실DB 명시적 CI red-line 회귀 테스트.

    `_RacyOutboxRepository`로 실 `_CLAIM_SQL`의 원자성만 제거하고 나머지는
    그대로 둔 채 3워커를 실 `order_command_outbox`/`orders`에 대고 돌린다.
    SELECT 후보 조회에는 잠금이 없으므로, 어떤 워커도 아직 커밋하지 않은
    시점에 3워커가 같은 PENDING 행들을 후보로 본다 — 그리고 UPDATE가
    `state='PENDING'`을 재확인하지 않으므로 그중 하나가 아니라 여러 워커가
    같은 행을 각자 "클레임"에 성공한다. 정상 `_CLAIM_SQL`(SKIP LOCKED가 같은
    문장 안에서 후보 선정과 잠금을 묶는다)에서는 절대 관측되지 않는 증상이다.
    이 증상이 실제로 나타남을 확인해, `test_three_workers_send_each_row_
    exactly_once_real_db`의 "행당 정확히 1회" 단언들이 실DB에서도 장식이
    아니라 회귀를 실제로 적색으로 만드는 게이트임을 증명한다.
    """
    _, _, order_ids, client_order_ids = await _setup_real_orders(pool, 10)
    order_repo, outbox_repo = PostgresOrderRepository(), _RacyOutboxRepository()
    adapter = ScriptedAdapter()

    async def resolve(tenant_id: UUID, exchange: str) -> ScriptedAdapter:
        return adapter

    dispatchers = [
        OutboxDispatcher(
            pool, outbox_repo=outbox_repo, order_repo=order_repo, resolve_adapter=resolve,
            pre_send_gate=allow_gate, worker_id=w,
        )
        for w in WORKERS
    ]

    reports = await asyncio.gather(*(d.dispatch_once(limit=10) for d in dispatchers))

    call_counts = Counter(c for c in adapter.calls if c in client_order_ids)
    duplicate_calls = sum(count - 1 for count in call_counts.values() if count > 1)
    conflicts = sum(r.conflicts for r in reports)
    assert duplicate_calls > 0 or conflicts > 0, (
        "실 claim_batch의 원자성을 깼는데도 중복 전송이나 펜스 충돌이 하나도 "
        "관측되지 않았다 — 이 파일의 정확히-1회 단언들이 실DB에서 회귀를 잡지 "
        "못하는 무력한 게이트일 수 있다(red-line 실패)."
    )
