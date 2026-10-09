"""open_order_sweeper 통합테스트 — 멱등성·동시성·어댑터 부분 실패.

Spec: docs/specs/L4_risk_and_safety_v1.0.md §3.8, §5(105번), §9(R-39).
FA-16(task-2406)이 지목한 DoD(c)(동시 2워커 SKIP LOCKED 보존) /
DoD(d)(TOCTOU race 노출)와 어댑터 부분 실패/누락 처리를 실제 Postgres 행으로
검증한다. scope 매핑/검증은 `test_open_order_sweeper.py`, order_events
원자성·수치 성능은 `test_open_order_sweeper_edge.py`로 분리했다(task-10872).
공유 fixture/헬퍼는 `conftest.py`.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

from src.foundation.risk_gate.domain.models import SafetyScope
from src.services.safety.open_order_sweeper import sweep_open_orders
from tests.integration.conftest import create_test_user
from tests.integration.fake_exchange_adapter import FakeExchangeAdapter
from tests.unit.services.conftest import (
    _adapters,
    _CountingCancelAdapter,
    _RaisingCancelAdapter,
    _seed_order,
    _status_of,
)


async def test_repeated_call_is_idempotent_and_does_not_recall_adapter(pool):
    user_id = await create_test_user(pool)
    order_id = await _seed_order(pool, user_id)
    adapter = _CountingCancelAdapter()
    control_id = uuid4()

    first = await sweep_open_orders(
        pool,
        {"bitget": adapter},
        control_id=control_id,
        scope=SafetyScope.TENANT,
        scope_ref=str(user_id),
    )
    second = await sweep_open_orders(
        pool,
        {"bitget": adapter},
        control_id=control_id,
        scope=SafetyScope.TENANT,
        scope_ref=str(user_id),
    )

    assert first.cancel_requested == (order_id,)
    assert second.cancel_requested == ()
    assert adapter.cancel_call_count == 1
    assert await _status_of(pool, order_id) == "CANCEL_REQUESTED"


async def test_adapter_failure_on_one_order_does_not_abort_sweep(pool):
    user_id = await create_test_user(pool)
    failing_order = await _seed_order(pool, user_id, exchange="bitget")
    ok_order = await _seed_order(pool, user_id, exchange="binance")
    failing_adapter = _RaisingCancelAdapter(exchange_name="bitget")

    report = await sweep_open_orders(
        pool,
        {"bitget": failing_adapter, "binance": FakeExchangeAdapter(exchange_name="binance")},
        control_id=uuid4(),
        scope=SafetyScope.TENANT,
        scope_ref=str(user_id),
    )

    assert set(report.cancel_requested) == {failing_order, ok_order}
    assert report.adapter_failed == (failing_order,)
    # 실패해도 상태를 되돌리지 않는다 — 결과는 reconcile에 위임(DoD 3).
    assert await _status_of(pool, failing_order) == "CANCEL_REQUESTED"
    assert await _status_of(pool, ok_order) == "CANCEL_REQUESTED"
    assert failing_adapter.cancel_call_count == 1


async def test_missing_adapter_for_exchange_is_reported_as_failed_not_raised(pool):
    user_id = await create_test_user(pool)
    order_id = await _seed_order(pool, user_id, exchange="bitget")

    report = await sweep_open_orders(
        pool, {}, control_id=uuid4(), scope=SafetyScope.TENANT, scope_ref=str(user_id)
    )

    assert report.cancel_requested == (order_id,)
    assert report.adapter_failed == (order_id,)
    assert await _status_of(pool, order_id) == "CANCEL_REQUESTED"


async def test_toctou_race_exposes_locked_order_in_raced(pool):
    """DoD(d) — 후보 선별 뒤 실제 전이 사이에 그 행이 이미 다른 트랜잭션에
    잠겨 있으면(SKIP LOCKED) 조용히 건너뛰고 raced에 노출한다(수치로 단언
    가능). 이벤트도 상태 변경도 남지 않는다(원래부터 매치 안 한 게 아니라
    경합으로 못 잡았다는 뜻)."""
    user_id = await create_test_user(pool)
    order_id = await _seed_order(pool, user_id)

    locker_conn = await pool.acquire()
    try:
        tx = locker_conn.transaction()
        await tx.start()
        await locker_conn.fetchrow(
            "SELECT status FROM orders WHERE order_id = $1 FOR UPDATE", order_id
        )
        try:
            report = await sweep_open_orders(
                pool,
                _adapters("bitget"),
                control_id=uuid4(),
                scope=SafetyScope.TENANT,
                scope_ref=str(user_id),
            )
            assert report.cancel_requested == ()
            assert report.raced == (order_id,)
        finally:
            await tx.rollback()
    finally:
        await pool.release(locker_conn)

    assert await _status_of(pool, order_id) == "SUBMITTED"
    async with pool.acquire() as conn:
        event_count = await conn.fetchval(
            "SELECT count(*) FROM order_events WHERE order_id = $1", order_id
        )
    assert event_count == 0


async def test_concurrent_sweeps_do_not_double_cancel_same_order(pool):
    """DoD(c) — 동시 2워커 sweep에서 같은 주문이 두 번 취소되지 않는다
    (SKIP LOCKED 동시성 보존, 재구현 아님). 정확히 한 쪽만 성공하고, 이벤트도
    정확히 1건만 남는다."""
    user_id = await create_test_user(pool)
    order_id = await _seed_order(pool, user_id)
    adapter_a = _CountingCancelAdapter()
    adapter_b = _CountingCancelAdapter()

    report_a, report_b = await asyncio.gather(
        sweep_open_orders(
            pool,
            {"bitget": adapter_a},
            control_id=uuid4(),
            scope=SafetyScope.TENANT,
            scope_ref=str(user_id),
        ),
        sweep_open_orders(
            pool,
            {"bitget": adapter_b},
            control_id=uuid4(),
            scope=SafetyScope.TENANT,
            scope_ref=str(user_id),
        ),
    )

    combined_cancel_requested = report_a.cancel_requested + report_b.cancel_requested
    assert combined_cancel_requested == (order_id,)
    assert adapter_a.cancel_call_count + adapter_b.cancel_call_count == 1
    assert await _status_of(pool, order_id) == "CANCEL_REQUESTED"

    async with pool.acquire() as conn:
        event_count = await conn.fetchval(
            "SELECT count(*) FROM order_events WHERE order_id = $1", order_id
        )
    assert event_count == 1
