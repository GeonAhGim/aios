"""EM-3 DEEPEN(task-9231) -- negative/실패주입 보강.

원 리프 task-6704(고아 산출물 회수)가 패키지 마커만 남기고 비워 둔 파일이다.
`test_parent_aggregation.py`/`test_algo_lifecycle.py`/
`test_committed_child_qty_replay.py`가 이미 이 디렉터리의 주된
negative/perf/동시성/replay 증거를 갖고 있으므로, 여기서는 그 파일들이
다루지 않은 세 공백만 메운다:

1. `ck_orders_committed_child_qty_bounds`/`ck_orders_filled_quantity_bounds`
   (마이그레이션 `c7f1e3a9d024`/`073beca589d5`) -- EM-A1(`assert_slice_within_
   parent_qty`)과 `aggregate_parent_state`의 `_assert_no_negative_fills`는
   둘 다 애플리케이션 계층 가드다. 이 두 CHECK 제약이 그 가드를 우회하는
   직접 UPDATE도 여전히 막아주는지(defense-in-depth) -- 기존 테스트는
   애플리케이션 가드만 검증하고 DB 제약 자체는 검증하지 않는다.
2. `reserve_child_slice`가 `test_parent_aggregation.py`에서 이미 다룬
   FILLED 외의 다른 terminal 상태(REJECTED)에서도 동일하게 거부하는지 --
   `assert_parent_accepts_new_child`는 `TERMINAL_ORDER_STATUSES` 전체를
   검사하지만 기존 테스트는 그중 한 값만 실측한다.
3. `recompute_parent_aggregate`가 예상치 못한 의존성 예외(자식 조회 계층의
   드라이버/네트워크 에러)를 삼키지 않고 그대로 전파하는지(fail-closed).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

import asyncpg
import pytest

from src.foundation.ems.application.aggregate_parent import (
    recompute_parent_aggregate,
    reserve_child_slice,
)
from src.foundation.ems.domain.parent_child import ParentTerminalError
from src.services.oms.adapters.order_repository import PostgresOrderRepository
from tests.integration.ems.test_parent_aggregation import _dsn, _insert_order
from tests.integration.oms.conftest import create_test_user


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_dsn(), min_size=1, max_size=4)
    yield p
    await p.close()


# ---------------------------------------------------------------------------
# Negative tests
# ---------------------------------------------------------------------------


async def test_db_check_constraint_rejects_committed_child_qty_over_parent_qty(pool):
    """`ck_orders_committed_child_qty_bounds`(c7f1e3a9d024) -- 애플리케이션
    가드(EM-A1 `assert_slice_within_parent_qty`)를 완전히 우회하는 직접
    UPDATE도 DB 자체가 막아야 한다. `reserve_child_slice`를 거치지 않고 raw
    SQL로 시도해 defense-in-depth를 실측한다."""
    user_id = await create_test_user(pool)
    parent_id = await _insert_order(pool, user_id, status="ACKNOWLEDGED", quantity=Decimal("10"))

    async with pool.acquire() as conn:
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                "UPDATE orders SET committed_child_qty = 11 WHERE order_id = $1", parent_id
            )


async def test_db_check_constraint_rejects_negative_filled_quantity(pool):
    """`ck_orders_filled_quantity_bounds`(073beca589d5) -- 애플리케이션
    가드(`aggregate_parent_state`의 `_assert_no_negative_fills`)를 완전히
    우회하는 직접 UPDATE도 DB 자체가 막아야 한다."""
    user_id = await create_test_user(pool)
    order_id = await _insert_order(pool, user_id, status="ACKNOWLEDGED", quantity=Decimal("10"))

    async with pool.acquire() as conn:
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                "UPDATE orders SET filled_quantity = -1 WHERE order_id = $1", order_id
            )


async def test_reserve_child_slice_rejects_rejected_parent_against_real_row(pool):
    """`test_parent_aggregation.py::test_reserve_child_slice_rejects_terminal_parent_against_real_row`
    는 FILLED만 실측한다 -- `TERMINAL_ORDER_STATUSES`의 다른 원소(REJECTED)도
    `assert_parent_accepts_new_child`가 동일하게 거부하는지 확인한다."""
    repo = PostgresOrderRepository()
    user_id = await create_test_user(pool)
    parent_id = await _insert_order(pool, user_id, status="REJECTED", quantity=Decimal("10"))

    async with pool.acquire() as conn:
        with pytest.raises(ParentTerminalError):
            await reserve_child_slice(
                repo, conn, parent_order_id=parent_id, new_slice_qty=Decimal("1")
            )


# ---------------------------------------------------------------------------
# Failure injection
# ---------------------------------------------------------------------------


async def test_recompute_parent_aggregate_propagates_unexpected_dependency_error(
    pool, monkeypatch: pytest.MonkeyPatch
):
    """`recompute_parent_aggregate`가 자식 조회 단계에서 터지는 예상치 못한
    의존성 예외(드라이버/네트워크 계층)를 삼키지 않고 그대로 호출자에게
    전파해야 한다(fail-closed, 조용한 성공으로 위장 금지)."""
    repo = PostgresOrderRepository()
    user_id = await create_test_user(pool)
    parent_id = await _insert_order(pool, user_id, status="ACKNOWLEDGED", quantity=Decimal("10"))

    async def _boom(self, conn, parent_order_id):
        raise RuntimeError("dependency exploded")

    monkeypatch.setattr(PostgresOrderRepository, "list_children_for_update", _boom)

    async with pool.acquire() as conn:
        with pytest.raises(RuntimeError, match="dependency exploded"):
            await recompute_parent_aggregate(
                repo,
                conn,
                parent_order_id=parent_id,
                trace_id=uuid.uuid4(),
                occurred_at=datetime.now(timezone.utc),
            )
