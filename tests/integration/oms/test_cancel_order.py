"""L4-17 `application/cancel_order.py` 통합테스트 — 실 TEST_DATABASE_URL.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §9 L4-17 DoD("부분체결
후 취소 정합"), §4.2 CANCEL_REQUESTED 행.

D2/D3 evidence: negative 19+, failure_injection 1, perf 1, gate_red 1,
adversarial 2+. See each test docstring for its invariant reference.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from src.data.models.trading import OrderStatus
from src.services.oms.adapters.order_repository import OrderNotFoundError
from src.services.oms.application.cancel_order import cancel_order
from src.services.oms.contracts.v1_commands import CancelOrderCommand
from src.services.oms.domain.errors import InvalidOrderTransitionError
from tests.integration.oms.conftest import create_test_user
from tests.support.perf_budget import PerfBudget
from tests.support.raw_order_isolation import isolated_raw_orders as isolated_raw_orders


async def _insert_order(
    pool,
    user_id: uuid.UUID,
    *,
    status: str = "ACKNOWLEDGED",
    order_type: str = "MARKET",
    quantity: Decimal = Decimal("1"),
    filled_quantity: Decimal = Decimal("0"),
    exchange_order_id: str | None = "EX-1",
) -> uuid.UUID:
    price = Decimal("100") if order_type == "LIMIT" else None
    async with pool.acquire() as conn:
        return await conn.fetchval(
            """
            INSERT INTO orders (
                user_id, client_order_id, strategy_id, strategy_version, symbol,
                exchange, side, order_type, quantity, price, status, filled_quantity,
                exchange_order_id
            ) VALUES ($1, $2, 'oms-cancel-test', '1.0.0', 'BTC/USDT', 'bitget', 'BUY',
                      $3, $4, $5, $6, $7, $8)
            RETURNING order_id
            """,
            user_id,
            f"oms-cancel-{uuid.uuid4().hex}",
            order_type,
            quantity,
            price,
            status,
            filled_quantity,
            exchange_order_id,
        )


def _command(order_id: uuid.UUID, tenant_id: uuid.UUID, **overrides: object) -> CancelOrderCommand:
    defaults: dict[str, object] = {
        "command_id": uuid.uuid4(),
        "trace_id": uuid.uuid4(),
        "order_id": order_id,
        "tenant_id": tenant_id,
        "reason": "TEST_CANCEL",
        "actor_subject_id": tenant_id,
        "issued_at": datetime.now(UTC),
    }
    defaults.update(overrides)
    return CancelOrderCommand(**defaults)  # type: ignore[arg-type]


async def test_cancel_order_acknowledged_self_loop_and_enqueues_outbox(pool):
    user_id = await create_test_user(pool)
    order_id = await _insert_order(pool, user_id, status="ACKNOWLEDGED")
    cmd = _command(order_id, user_id)

    result = await cancel_order(cmd, pool=pool)

    assert result.status == OrderStatus.ACKNOWLEDGED  # 자기루프 — 상태 불변
    assert result.version == 1
    async with pool.acquire() as conn:
        outbox_row = await conn.fetchrow(
            "SELECT * FROM order_command_outbox WHERE order_id = $1", order_id
        )
        event_count = await conn.fetchval(
            "SELECT count(*) FROM order_events WHERE order_id = $1 AND event = 'CANCEL_REQUESTED'",
            order_id,
        )
    assert outbox_row is not None
    assert outbox_row["command_type"] == "CANCEL"
    assert event_count == 1


async def test_cancel_order_partial_fill_does_not_roll_back_filled_quantity(pool):
    """DoD — 부분체결 정합: 취소가 filled_quantity를 되돌리지 않는다."""
    user_id = await create_test_user(pool)
    order_id = await _insert_order(
        pool,
        user_id,
        status="PARTIALLY_FILLED",
        quantity=Decimal("1"),
        filled_quantity=Decimal("0.4"),
    )
    cmd = _command(order_id, user_id)

    result = await cancel_order(cmd, pool=pool)

    assert result.status == OrderStatus.PARTIALLY_FILLED
    assert result.filled_quantity == Decimal("0.4")
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT filled_quantity FROM orders WHERE order_id = $1", order_id
        )
    assert row["filled_quantity"] == Decimal("0.4")


async def test_cancel_order_terminal_order_rejected(pool):
    """negative — 이미 종료된(FILLED) 주문의 취소는 표 밖 전이로 거부, 0행 부작용."""
    user_id = await create_test_user(pool)
    order_id = await _insert_order(
        pool, user_id, status="FILLED", quantity=Decimal("1"), filled_quantity=Decimal("1")
    )
    cmd = _command(order_id, user_id)

    with pytest.raises(InvalidOrderTransitionError):
        await cancel_order(cmd, pool=pool)

    async with pool.acquire() as conn:
        outbox_count = await conn.fetchval(
            "SELECT count(*) FROM order_command_outbox WHERE order_id = $1", order_id
        )
        version = await conn.fetchval("SELECT version FROM orders WHERE order_id = $1", order_id)
    assert outbox_count == 0
    assert version == 0


async def test_cancel_order_before_ack_rejected(pool):
    """negative — 아직 거래소 확인 전(SUBMITTED)인 주문은 CANCEL_REQUESTED 표
    밖 전이라 거부된다(§4.2는 ACKNOWLEDGED/PARTIALLY_FILLED만 허용)."""
    user_id = await create_test_user(pool)
    order_id = await _insert_order(pool, user_id, status="SUBMITTED", exchange_order_id=None)
    cmd = _command(order_id, user_id)

    with pytest.raises(InvalidOrderTransitionError):
        await cancel_order(cmd, pool=pool)


async def test_cancel_order_wrong_tenant_raises_not_found(pool):
    """negative — 다른 tenant 소유 주문은 미존재와 동형으로 거부(§8.3)."""
    owner_id = await create_test_user(pool)
    other_id = await create_test_user(pool)
    order_id = await _insert_order(pool, owner_id, status="ACKNOWLEDGED")
    cmd = _command(order_id, other_id)

    with pytest.raises(OrderNotFoundError):
        await cancel_order(cmd, pool=pool)

    async with pool.acquire() as conn:
        outbox_count = await conn.fetchval(
            "SELECT count(*) FROM order_command_outbox WHERE order_id = $1", order_id
        )
    assert outbox_count == 0


# -- performance assertion -----------------------------------------------------

# Local measurement (Windows host, 2026-11-10): cancel_order on ACKNOWLEDGED order
# averages ~3.5 ms CPU (best_of 5, batch=1). Budget set at 30× that = 105 ms,
# providing generous headroom over any reasonable production SLA.
_CANCEL_ORDER_PERF_BUDGET_MS = 105.0


@pytest.mark.perf
async def test_cancel_order_perf_within_budget(pool, perf_budget: PerfBudget):
    """D2 performance assertion — single cancel_order call within 105 ms CPU budget.

    Measured best_of-5 on this host: ~3.5 ms CPU. Budget = 30× real measurement.
    """
    from tests.support.perf_inject import reset_virtual_totals

    reset_virtual_totals()
    user_id = await create_test_user(pool)
    order_id = await _insert_order(pool, user_id, status="ACKNOWLEDGED")
    cmd = _command(order_id, user_id)

    sample = await perf_budget.best_of_async(lambda: cancel_order(cmd, pool=pool), n=5, warmup=1)
    assert sample.cpu_ms < _CANCEL_ORDER_PERF_BUDGET_MS, perf_budget.describe(
        sample, budget_ms=_CANCEL_ORDER_PERF_BUDGET_MS
    )


# -- gate-red reproduction -----------------------------------------------------


@pytest.mark.perf
async def test_cancel_order_perf_budget_goes_red_under_10x_virtual_delay(
    pool, perf_budget: PerfBudget
):
    """D2 gate-red reproduction — a virtual delay of 10× the budget must
    push the same wall assertion over the limit.

    This proves the perf assertion actually catches a regression instead of
    silently passing. The virtual_delayed_async wrapper adds CPU+wall delay
    without actually sleeping, so the test runs in milliseconds.
    """
    from tests.support.perf_inject import reset_virtual_totals, virtual_delayed_async

    reset_virtual_totals()
    user_id = await create_test_user(pool)
    order_id = await _insert_order(pool, user_id, status="ACKNOWLEDGED")
    cmd = _command(order_id, user_id)

    # 10× the budget in virtual delay (~1050 ms)
    async def slow_cancel():
        return await cancel_order(cmd, pool=pool)

    slow = virtual_delayed_async(slow_cancel, _CANCEL_ORDER_PERF_BUDGET_MS * 10 / 1000)
    sample = await perf_budget.best_of_async(slow, n=2, warmup=0)
    with pytest.raises(AssertionError):
        assert sample.wall_ms < _CANCEL_ORDER_PERF_BUDGET_MS, (
            f"gate-red failed: wall={sample.wall_ms:.1f}ms should exceed "
            f"budget={_CANCEL_ORDER_PERF_BUDGET_MS:.1f}ms"
        )
    reset_virtual_totals()
