"""FA-16 — adversarial: `sweep_open_orders`(kill switch R-39) never changes
`orders.status` without a matching `order_events` row, in the same
transaction.

Spec: docs/specs/ibor_fund_accounting_and_resilience.md#§9 FA-16 +
docs/specs/L4_risk_and_safety_v1.0.md §9(R-39). task-2394가 진단한 결함:
원래 `open_order_sweeper.sweep_open_orders()`는 매칭되는 모든 행을 단일
`UPDATE ... RETURNING`으로 한 번에 `CANCEL_REQUESTED`로 전이시켰고,
`order_events` INSERT가 전혀 없었다(replay_verify의 orders 투영 불일치
원인). 지금은 주문 단위 트랜잭션(`_transition_to_cancel_requested`)이
§5.1 순서(`SET LOCAL` → `order_events` INSERT → 조건부 UPDATE)를 매 주문마다
새로 밟는다.

DoD(a) "재현" — `test_sweep_open_orders_each_transitioned_order_has_an_
order_event`는 `orders.status`가 바뀐 주문마다 `order_events` 행이
있어야 한다고 단언한다. 이 단언을 지우지 않고, `open_order_sweeper.py`의
`_transition_to_cancel_requested()`에서 `INSERT INTO order_events` 호출을
임시로 주석 처리하고 이 테스트가 FAIL하는 것을 확인한 뒤 원복했다(회귀
방지를 위해 sabotage 코드는 커밋에 남기지 않는다) — bulk UPDATE로
되돌리면 이 테스트가 다시 FAIL한다는 뜻.

DoD(b) — 073beca589d5의 `oms_enforce_order_transition_trg`(I6)를
우회하지 않는다는 증거. 이 테스트가 작성될 당시(task-2406) `CANCEL_REQUESTED`는
073beca589d5의 §4.2 `_ALLOWED_PAIRS`에 없는 kill-switch 전용 상태값이라,
cutover가 무장되면 I2(전이표)에서 먼저 막혀 I6까지 도달하지 못했다 — 그래서
`_ALLOWED_PAIRS`에 이미 있던 일반 전이(CREATED->VALIDATED)로 I6 자체의
매커니즘만 증명했다. task-2432가 `CANCEL_REQUESTED`를 실제 `OrderStatus`
멤버로 승격하고 `_ALLOWED_PAIRS`에도 등재했지만(migration f93d241b4ab6),
이 테스트는 여전히 CREATED->VALIDATED로 남긴다 — I6 메커니즘 자체("플래그
없이 UPDATE하면 막힌다", "플래그는 한 행에서 소진된다")는 어느 전이쌍으로
증명해도 동일하고, 바꿀 이유가 없다. `sweep_open_orders`가 배치 전체에
플래그를 한 번만 세우면(원래 코드가 이랬다면) 두 번째 행부터 이 트리거에
막혔을 것이라는 근거다.
"""
from __future__ import annotations

from uuid import UUID, uuid4

import asyncpg
import pytest

from src.data.models.trading import OrderStatus
from src.foundation.risk_gate.domain.models import SafetyScope
from src.services.oms.adapters.order_events_repository import PostgresOrderEventRepository
from src.services.safety.open_order_sweeper import sweep_open_orders
from tests.integration.conftest import create_test_user
from tests.integration.fake_exchange_adapter import FakeExchangeAdapter
from tests.integration.oms.conftest import arm_cutover_sql, insert_event, insert_order


async def _seed_order(pool: asyncpg.Pool, user_id: UUID) -> UUID:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO orders (
                user_id, client_order_id, exchange_order_id, strategy_id,
                strategy_version, symbol, exchange, side, order_type,
                quantity, status
            ) VALUES (
                $1, $2, $3, 'fa16-sweep-adv', '1.0.0', 'BTC/USDT', 'bitget', 'BUY',
                'LIMIT', 1.0, 'SUBMITTED'
            )
            RETURNING order_id
            """,
            user_id,
            f"fa16-sweep-{uuid4().hex}",
            f"ex-{uuid4().hex[:12]}",
        )
    return row["order_id"]


async def test_sweep_open_orders_each_transitioned_order_has_an_order_event(pool):
    """DoD(a) — positive/재현. sweep으로 status가 바뀐 주문마다 order_events가
    최소 1건 있어야 한다(원래 bulk UPDATE 코드는 0건이라 이 단언이 FAIL했다)."""
    user_id = await create_test_user(pool)
    order_id = await _seed_order(pool, user_id)

    report = await sweep_open_orders(
        pool,
        {"bitget": FakeExchangeAdapter(exchange_name="bitget")},
        control_id=uuid4(),
        scope=SafetyScope.TENANT,
        scope_ref=str(user_id),
    )
    assert report.cancel_requested == (order_id,)

    async with pool.acquire() as conn:
        status = await conn.fetchval("SELECT status FROM orders WHERE order_id = $1", order_id)
        event_count = await conn.fetchval(
            "SELECT count(*) FROM order_events WHERE order_id = $1", order_id
        )
    assert status == "CANCEL_REQUESTED"
    assert event_count >= 1, (
        f"order {order_id}: status changed to CANCEL_REQUESTED but order_events has "
        f"{event_count} rows — FA-16 무이벤트 상태변경(I-10 위반)."
    )


async def test_sweep_open_orders_event_does_not_crash_replay_verify_timeline_read(pool):
    """task-2432 (closes task-2406 DoD(e)) — `CANCEL_REQUESTED` is now a real
    `OrderStatus` member, so `order_events.to_status` carries it directly
    (a genuine transition, no longer the self-loop workaround this test used
    to guard). `PostgresOrderEventRepository.timeline()`
    (`scripts/replay_verify.py` calls it for every order a sweep touches)
    must still not crash -- this is the regression this test protects,
    now proven against the real value instead of the self-loop stand-in."""
    user_id = await create_test_user(pool)
    order_id = await _seed_order(pool, user_id)

    await sweep_open_orders(
        pool,
        {"bitget": FakeExchangeAdapter(exchange_name="bitget")},
        control_id=uuid4(),
        scope=SafetyScope.TENANT,
        scope_ref=str(user_id),
    )

    async with pool.acquire() as conn:
        events = await PostgresOrderEventRepository().timeline(conn, order_id)
    assert len(events) == 1
    assert events[0].from_status.value == "SUBMITTED"
    assert events[0].to_status == OrderStatus.CANCEL_REQUESTED


async def test_trigger_blocks_update_without_flag_and_flag_is_consumed_per_row(pool):
    """DoD(b) — I6 트리거 우회 없음의 증거. 무장된 cutover 아래서 `oms.
    event_written` 플래그 없이 orders.status를 바꾸면 트리거가 막는다. 플래그는
    세운 그 UPDATE 한 건에만 유효하고 즉시 소진된다 — 같은 트랜잭션의 다음
    행에 재사용할 수 없다. 이게 바로 `sweep_open_orders`가 배치 전체에 한 번이
    아니라 주문마다 새로 플래그를 세우는 이유다."""
    async with pool.acquire() as conn:
        user_id = await create_test_user(pool)
        tr = conn.transaction()
        await tr.start()
        try:
            await conn.execute(arm_cutover_sql)
            order_a = await insert_order(conn, user_id, status="CREATED")
            order_b = await insert_order(conn, user_id, status="CREATED")

            with pytest.raises(asyncpg.CheckViolationError, match="without order_events"):
                async with conn.transaction():
                    await conn.execute(
                        "UPDATE orders SET status = 'VALIDATED' WHERE order_id = $1", order_a
                    )

            # §5.1 순서대로 플래그 -> 이벤트 -> UPDATE면 통과.
            await conn.execute("SELECT set_config('oms.event_written', '1', true)")
            await insert_event(
                conn, order_a, from_status="CREATED", to_status="VALIDATED", event="VALIDATED"
            )
            await conn.execute(
                "UPDATE orders SET status = 'VALIDATED' WHERE order_id = $1", order_a
            )
            status_a = await conn.fetchval(
                "SELECT status FROM orders WHERE order_id = $1", order_a
            )
            assert status_a == "VALIDATED"

            # 같은 트랜잭션의 두 번째 행 — 플래그를 다시 세우지 않으면 막힌다
            # (배치 전체에 한 번만 세우는 방식이 왜 틀렸는지의 직접 증거).
            with pytest.raises(asyncpg.CheckViolationError, match="without order_events"):
                async with conn.transaction():
                    await conn.execute(
                        "UPDATE orders SET status = 'VALIDATED' WHERE order_id = $1", order_b
                    )
        finally:
            await tr.rollback()
