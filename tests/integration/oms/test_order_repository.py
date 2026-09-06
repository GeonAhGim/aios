"""L4-07 `order_repository`/`order_events_repository` 실DB 통합테스트.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §9 L4-07
("전이+이벤트 1tx, 이벤트 없는 전이 RAISE 재현"), §4.1 I2/I5/I6, §4.2, §5.1.

L4-06(073beca589d5)의 I2/I4/I6 트리거는 `oms_order_transition_cutover`가
무장된 뒤에만 적용된다 — 이 파일에서 그 트리거 자체를 다시 증명하는 테스트
(이벤트 없는 UPDATE RAISE)만 무장한 트랜잭션 안에서 실행하고 항상 롤백한다
(tests/integration/oms/test_db_transition_trigger.py와 동일 관례). I3/I5는
cutover 무관 항상 강제되므로 나머지 테스트는 무장하지 않는다.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import UUID, uuid4

import asyncpg
import pytest

from src.core.db.conditional_write import ConcurrencyConflictError
from src.data.models.trading import OrderStatus
from src.services.oms.adapters.order_events_repository import PostgresOrderEventRepository
from src.services.oms.adapters.order_repository import OrderNotFoundError, PostgresOrderRepository
from src.services.oms.contracts.v1_events import OrderTransitionEvent
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
