"""L4-07 `order_repository`/`order_events_repository` 실DB 통합테스트.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §9 L4-07
("전이+이벤트 1tx, 이벤트 없는 전이 RAISE 재현"), §4.1 I2/I5/I6, §4.2, §5.1.

L4-06(073beca589d5)의 I2/I4/I6 트리거는 `oms_order_transition_cutover`가
무장된 뒤에만 적용된다 — 이 파일에서 그 트리거 자체를 다시 증명하는 테스트
(이벤트 없는 UPDATE RAISE)만 무장한 트랜잭션 안에서 실행하고 항상 롤백한다
(tests/integration/oms/test_db_transition_trigger.py와 동일 관례). I3/I5는
cutover 무관 항상 강제되므로 나머지 테스트는 무장하지 않는다.

DEPTH_L4_BR(task-2722)가 원 리프(task-1564, 13f805a7)를 D1로 판정 — negative
5종·원자성 롤백·2워커 레이스 증명은 강하지만 (a) 수치 성능/지연 단언(D2)이
없고, (b) 게이트가 실제로 회귀를 잡는지 보이는 명시적 CI red-line 테스트가
없고, (c) 동시성 증명이 2워커에 그쳐 D3 하한(다중 워커/adversarial)에
못 미친다는 지적이었다. task-2763 DEEPEN으로 아래 세 가지를 추가한다:
- `test_transition_round_trip_count_is_bounded_and_stable` — `transition()`
  1회의 순차 DB 왕복 수를 정확 단언(CI 차단 게이트, 절대시간은 print 비차단
  — `tests/performance/oms/test_outbox_dispatch_latency.py`(task-2323)와
  동일 decision).
- `test_concurrent_transitions_many_workers_exactly_one_succeeds` — 같은
  주문에 6워커가 동시에 전이를 시도해도 정확히 1승 5패(`ConcurrencyConflictError`)임을
  증명한다(기존 2워커 증명의 D3 확장).
- `_LockFreeOrderRepository` +
  `test_missing_row_lock_lets_conflicting_transition_write_orphan_event` —
  `get_for_update`의 `FOR UPDATE` 잠금만 제거한 변형으로 같은 레이스를
  재현해, 이 파일의 "정확히 1승" 단언이 장식이 아니라 실제로 그 잠금에
  의존하는 게이트임을 증명하는 red-line(잠금 없이는 패자의 `order_events`
  행이 최종 주문 상태와 불일치한 orphan으로 남는다).
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from uuid import UUID, uuid4

import asyncpg
import pytest

from src.core.db.conditional_write import ConcurrencyConflictError
from src.data.models.trading import OrderStatus
from src.services.oms.adapters.order_events_repository import PostgresOrderEventRepository
from src.services.oms.adapters.order_repository import (
    OrderNotFoundError,
    PostgresOrderRepository,
    _row_to_view,
)
from src.services.oms.contracts.v1_events import OrderTransitionEvent
from src.services.oms.contracts.v1_views import OrderView
from src.services.oms.ports.repository import OrderEventRepoPort, OrderRepoPort
from tests.integration.oms.conftest import arm_cutover_sql, create_test_user, insert_order


def _transition_event(
    order_id: UUID, *, from_status: str, to_status: str, event: str
) -> OrderTransitionEvent:
    return OrderTransitionEvent(
        order_id=order_id,
        from_status=OrderStatus(from_status),
        to_status=OrderStatus(to_status),
        event=event,
        reason_code=None,
        actor_subject_id="system",
        trace_id=uuid4(),
        command_id=None,
        provider_event_id=None,
        occurred_at=datetime.now(timezone.utc),
        payload_hash="e" * 64,
    )


def test_adapters_satisfy_repository_ports() -> None:
    """decision — `test_repository_ports.py`의 `@runtime_checkable` 계약을
    이 리프의 실제 구현체에 대해서도 증명한다."""
    assert isinstance(PostgresOrderRepository(), OrderRepoPort)
    assert isinstance(PostgresOrderEventRepository(), OrderEventRepoPort)


async def test_transition_writes_event_and_updates_order_in_one_tx(pool):
    repo = PostgresOrderRepository()
    user_id = await create_test_user(pool)
    async with pool.acquire() as conn:
        order_id = await insert_order(conn, user_id, status="CREATED")
        ev = _transition_event(
            order_id, from_status="CREATED", to_status="VALIDATED", event="VALIDATED"
        )
        view = await repo.transition(
            conn,
            order_id=order_id,
            expected_status=OrderStatus.CREATED,
            expected_version=0,
            new_status=OrderStatus.VALIDATED,
            patch={},
            event=ev,
        )
        assert view.status is OrderStatus.VALIDATED
        assert view.version == 1

        status = await conn.fetchval("SELECT status FROM orders WHERE order_id = $1", order_id)
        version = await conn.fetchval("SELECT version FROM orders WHERE order_id = $1", order_id)
        event_row = await conn.fetchrow(
            "SELECT to_status, event FROM order_events WHERE order_id = $1", order_id
        )
        audit_count = await conn.fetchval(
            "SELECT COUNT(*) FROM audit_log WHERE target_id = $1", str(order_id)
        )
    assert status == "VALIDATED"
    assert version == 1
    assert event_row["to_status"] == "VALIDATED"
    assert event_row["event"] == "VALIDATED"
    assert audit_count == 1


async def test_transition_and_event_are_atomic_rollback_removes_both(pool):
    """DoD — 전이+이벤트 원자성: 같은 트랜잭션이 이후 다른 이유로 롤백되면
    order_events INSERT와 orders UPDATE가 함께 사라진다."""
    repo = PostgresOrderRepository()
    async with pool.acquire() as conn:
        user_id = await create_test_user(pool)
        outer = conn.transaction()
        await outer.start()
        try:
            order_id = await insert_order(conn, user_id, status="CREATED")
            ev = _transition_event(
                order_id, from_status="CREATED", to_status="VALIDATED", event="VALIDATED"
            )
            with pytest.raises(RuntimeError, match="simulated downstream failure"):
                async with conn.transaction():
                    await repo.transition(
                        conn,
                        order_id=order_id,
                        expected_status=OrderStatus.CREATED,
                        expected_version=0,
                        new_status=OrderStatus.VALIDATED,
                        patch={},
                        event=ev,
                    )
                    raise RuntimeError("simulated downstream failure")

            status = await conn.fetchval(
                "SELECT status FROM orders WHERE order_id = $1", order_id
            )
            version = await conn.fetchval(
                "SELECT version FROM orders WHERE order_id = $1", order_id
            )
            event_count = await conn.fetchval(
                "SELECT COUNT(*) FROM order_events WHERE order_id = $1", order_id
            )
            assert status == "CREATED"
            assert version == 0
            assert event_count == 0
        finally:
            await outer.rollback()


async def test_status_update_without_order_event_raises_once_cutover_armed(pool):
    """DoD — '이벤트 없는 전이 RAISE 재현'. `order_repository.transition()`이
    항상 SET LOCAL+INSERT를 먼저 하는 이유는 장식이 아니라, DB가 그 순서를
    빼먹은 UPDATE를 073beca589d5의 I6 트리거로 실제 거부하기 때문이다."""
    async with pool.acquire() as conn:
        user_id = await create_test_user(pool)
        tr = conn.transaction()
        await tr.start()
        try:
            await conn.execute(arm_cutover_sql)
            order_id = await insert_order(conn, user_id, status="CREATED")
            with pytest.raises(asyncpg.CheckViolationError, match="without order_events"):
                async with conn.transaction():
                    await conn.execute(
                        "UPDATE orders SET status = 'VALIDATED' WHERE order_id = $1", order_id
                    )
        finally:
            await tr.rollback()


async def test_concurrent_transitions_only_one_succeeds(pool):
    """DoD — 동시 2전이 1승 1실패(version CAS). `get_for_update`의 FOR UPDATE가
    두 트랜잭션을 직렬화하고, 나중 트랜잭션은 이미 바뀐 status/version을 보고
    `ConcurrencyConflictError`로 거부된다(§3.4 OMS_CONCURRENCY_CONFLICT)."""
    repo = PostgresOrderRepository()
    user_id = await create_test_user(pool)
    async with pool.acquire() as conn:
        order_id = await insert_order(conn, user_id, status="CREATED")

    async def attempt(to_status: OrderStatus, event_name: str) -> OrderStatus:
        async with pool.acquire() as conn, conn.transaction():
            ev = _transition_event(
                order_id, from_status="CREATED", to_status=to_status.value, event=event_name
            )
            view = await repo.transition(
                conn,
                order_id=order_id,
                expected_status=OrderStatus.CREATED,
                expected_version=0,
                new_status=to_status,
                patch={},
                event=ev,
            )
            return view.status

    results = await asyncio.gather(
        attempt(OrderStatus.VALIDATED, "VALIDATED"),
        attempt(OrderStatus.FAILED, "VALIDATION_FAILED"),
        return_exceptions=True,
    )
    successes = [r for r in results if not isinstance(r, Exception)]
    failures = [r for r in results if isinstance(r, ConcurrencyConflictError)]
    assert len(successes) == 1
    assert len(failures) == 1


async def test_get_for_update_missing_order_raises_not_found(pool):
    repo = PostgresOrderRepository()
    missing_id = uuid4()
    async with pool.acquire() as conn:
        with pytest.raises(OrderNotFoundError):
            await repo.get_for_update(conn, missing_id)


async def test_get_for_update_cross_tenant_lookup_raises_same_not_found_as_missing(pool):
    """decision — '교차 tenant get_for_update 404 동형' negative. 포트에
    tenant 인자가 없으므로(§2-C), 실제로 tenant B가 tenant A 소유 id를 잘못
    짚어 조회하는 시나리오는 이 어댑터가 아니라 application 계층이 막을
    책임이다 — 여기선 그 경계 밖 id(존재하지 않는 id)가 실제로 존재하지
    않는 id와 완전히 같은 모양(404)으로 거부됨만 증명한다."""
    repo = PostgresOrderRepository()
    tenant_a = await create_test_user(pool)
    async with pool.acquire() as conn:
        await insert_order(conn, tenant_a)  # tenant A 소유 주문 — 실제로 존재한다

    other_tenant_order_id = uuid4()  # tenant B가 잘못/악의적으로 조회한 남의 id
    async with pool.acquire() as conn:
        with pytest.raises(OrderNotFoundError) as exc_info:
            await repo.get_for_update(conn, other_tenant_order_id)
    assert str(other_tenant_order_id) in str(exc_info.value)


# `transition()` 1회의 순차 DB 왕복 구성(실측, /tmp 스크립트로 확인, 이 순서
# 그대로) — BEGIN 1 + get_for_update 1 + set_config 1 + order_events INSERT 1
# + conditional UPDATE 1 + audit_bridge.emit(advisory lock 1 + prev-hash
# SELECT 1 + audit_log INSERT 1 + foundation_audit_event INSERT 1 = 4) = 9.
# asyncpg의 쿼리 로거는 이 커넥션의 `conn.transaction()`이 발행하는 COMMIT은
# 잡지 않는다(BEGIN만 잡힌다) — 실측 그대로 예산에 반영했다.
_TRANSITION_ROUND_TRIP_BUDGET = 9


async def test_transition_round_trip_count_is_bounded_and_stable(pool):
    """DEPTH_L4_BR(task-2722) D2 — 수치 성능/지연 단언(CI 차단 게이트).

    절대 벽시계 시간은 실DB I/O라 환경(CPU/디스크/네트워크) 편차에 노출되므로
    print로만 보고한다(`test_outbox_dispatch_latency.py` task-1038/1521
    decision과 동일) — CI를 실제로 차단하는 수치 단언은 순차 DB 왕복 수의
    정확 일치다. 이 수가 늘면 `transition()`이 새 라운드트립을 추가한
    구조적 회귀(예: N+1 쿼리, 불필요한 재조회)다.
    """
    repo = PostgresOrderRepository()
    user_id = await create_test_user(pool)
    async with pool.acquire() as conn:
        order_id = await insert_order(conn, user_id, status="CREATED")
        ev = _transition_event(
            order_id, from_status="CREATED", to_status="VALIDATED", event="VALIDATED"
        )

        queries: list[object] = []
        conn.add_query_logger(lambda record: queries.append(record))

        async with conn.transaction():
            started = time.perf_counter()
            await repo.transition(
                conn,
                order_id=order_id,
                expected_status=OrderStatus.CREATED,
                expected_version=0,
                new_status=OrderStatus.VALIDATED,
                patch={},
                event=ev,
            )
            elapsed_ms = (time.perf_counter() - started) * 1000.0
        # 왕복 수는 `pool.acquire()` 블록을 벗어나기 전에 스냅샷한다 — 이후
        # `pool.release()`가 트리거하는 asyncpg 커넥션 리셋(advisory lock을
        # 쓴 커넥션에 방어적으로 `SELECT pg_advisory_unlock_all()`을 추가
        # 발행, `asyncpg/connection.py` `_get_reset_query`)이 이 함수 자체와
        # 무관한 왕복을 더해 예산을 오염시킨다(실측으로 확인).
        round_trips = len(queries)

    print(
        f"\norder_repository.transition() 1회: elapsed={elapsed_ms:.2f}ms "
        f"(비차단, 환경 편차) round_trips={round_trips} "
        f"(budget=={_TRANSITION_ROUND_TRIP_BUDGET}, CI 차단)"
    )
    assert round_trips == _TRANSITION_ROUND_TRIP_BUDGET, (
        f"transition() 순차 DB 왕복 수({round_trips})가 예산"
        f"({_TRANSITION_ROUND_TRIP_BUDGET})과 다릅니다 — 구조 변경입니다"
        "(모듈 docstring 구성표를 갱신하고 리뷰를 받으세요)."
    )


async def test_concurrent_transitions_many_workers_exactly_one_succeeds(pool):
    """DEPTH_L4_BR(task-2722) D3 — 2워커 증명을 다중(6) 워커로 확장.

    `test_concurrent_transitions_only_one_succeeds`(2워커)의 D3 하한 미달
    지적에 대한 직접 응답 — 서로 다른 목표 상태(VALIDATED×3, FAILED×3)로
    같은 `expected_version=0`을 놓고 6개의 독립 커넥션이 동시에 경합해도
    정확히 1승 5패(`ConcurrencyConflictError`)임을 증명한다."""
    repo = PostgresOrderRepository()
    user_id = await create_test_user(pool)
    async with pool.acquire() as conn:
        order_id = await insert_order(conn, user_id, status="CREATED")

    async def attempt(to_status: OrderStatus, event_name: str) -> OrderStatus:
        async with pool.acquire() as conn, conn.transaction():
            ev = _transition_event(
                order_id, from_status="CREATED", to_status=to_status.value, event=event_name
            )
            view = await repo.transition(
                conn,
                order_id=order_id,
                expected_status=OrderStatus.CREATED,
                expected_version=0,
                new_status=to_status,
                patch={},
                event=ev,
            )
            return view.status

    targets = [OrderStatus.VALIDATED] * 3 + [OrderStatus.FAILED] * 3
    results = await asyncio.gather(
        *(attempt(status, f"w{i}") for i, status in enumerate(targets)),
        return_exceptions=True,
    )
    successes = [r for r in results if not isinstance(r, Exception)]
    failures = [r for r in results if isinstance(r, ConcurrencyConflictError)]
    assert len(successes) == 1
    assert len(failures) == 5


class _LockFreeOrderRepository(PostgresOrderRepository):
    """CI red-line 전용(D3, task-2763) — `get_for_update`의 `FOR UPDATE`
    잠금만 제거한다(나머지 `transition()` 순서는 그대로). `asyncio.sleep`으로
    레이스 창을 넓혀, 이 파일의 "정확히 1승" 단언들이 실제로 그 잠금에
    의존하는 게이트임을 증명한다 — 실운영 코드에는 이 지연이 없다."""

    async def get_for_update(self, conn: asyncpg.Connection, order_id: UUID) -> OrderView:
        row = await conn.fetchrow("SELECT * FROM orders WHERE order_id = $1", order_id)
        if row is None:
            raise OrderNotFoundError(str(order_id))
        await asyncio.sleep(0.05)  # 회귀 주입 — 다른 워커가 같은 CREATED 스냅샷을 본다
        return _row_to_view(row)


async def test_missing_row_lock_lets_conflicting_transition_write_orphan_event(pool):
    """DEPTH_L4_BR(task-2722) — 명시적 CI red-line 회귀 테스트.

    `_LockFreeOrderRepository`로 `get_for_update`의 행 잠금만 제거하고 동시
    전이 2건을 재현한다. `conditional_update`의 버전 CAS(§5.1)는 여전히
    한쪽을 `ConcurrencyConflictError`로 정확히 거부하지만(수치가 같아
    "안전해 보임"), 잠금이 없으므로 두 워커 모두 잠금 창 안에서 `order_events`
    INSERT까지 끝낸 뒤에야 패자가 걸린다 — 패자의 이벤트 행이 실제로는
    일어나지 않은 전이("VALIDATION_FAILED")를 주장하는 orphan으로 남는다.
    이 증상은 정상 `PostgresOrderRepository`에서는 절대 나타나지 않는다
    (`test_concurrent_transitions_*`가 매번 이벤트 수==성공 수임을 암묵
    전제한다) — 이 테스트가 그 전제가 실제로 잠금에 의존함을 확인한다.
    """
    repo = _LockFreeOrderRepository()
    user_id = await create_test_user(pool)
    async with pool.acquire() as conn:
        order_id = await insert_order(conn, user_id, status="CREATED")

    async def attempt(to_status: OrderStatus, event_name: str) -> OrderStatus:
        async with pool.acquire() as conn:
            ev = _transition_event(
                order_id, from_status="CREATED", to_status=to_status.value, event=event_name
            )
            view = await repo.transition(
                conn,
                order_id=order_id,
                expected_status=OrderStatus.CREATED,
                expected_version=0,
                new_status=to_status,
                patch={},
                event=ev,
            )
            return view.status

    results = await asyncio.gather(
        attempt(OrderStatus.VALIDATED, "VALIDATED"),
        attempt(OrderStatus.FAILED, "VALIDATION_FAILED"),
        return_exceptions=True,
    )
    successes = [r for r in results if not isinstance(r, Exception)]
    failures = [r for r in results if isinstance(r, ConcurrencyConflictError)]
    assert len(successes) == 1
    assert len(failures) == 1

    async with pool.acquire() as conn:
        event_count = await conn.fetchval(
            "SELECT COUNT(*) FROM order_events WHERE order_id = $1", order_id
        )
        final_status = await conn.fetchval(
            "SELECT status FROM orders WHERE order_id = $1", order_id
        )
    assert final_status == successes[0].value
    assert event_count == 2, (
        "잠금 없이도 order_events가 성공 건수(1)만큼만 남았다 — 이 red-line이 "
        "더 이상 회귀를 재현하지 못합니다(레이스 창 조정이 필요할 수 있습니다)."
    )
