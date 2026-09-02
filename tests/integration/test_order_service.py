"""FD-4 통합테스트 — 실제 dev/test DB 대상, 거래소는 FakeExchangeAdapter로 대역."""
import asyncio
import json
import uuid
from decimal import Decimal
from pathlib import Path

import asyncpg
import pytest
from dotenv import dotenv_values

from src.core.db.conditional_write import ConcurrencyConflictError
from src.core.exceptions import RetryableExchangeError
from src.data.models.base import AssetClass
from src.data.models.trading import Order, OrderSide, OrderStatus, OrderType
from src.services.order_service import (
    OrderCancelError,
    OrderModifyError,
    cancel_order,
    modify_order,
    resolve_unknown,
    submit_order,
)
from src.services.order_service import repository as order_repository
from tests.integration.conftest import create_test_user
from tests.integration.fake_exchange_adapter import FakeExchangeAdapter


def _asyncpg_dsn() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[2] / ".env")
    url = env.get("DATABASE_URL")
    assert url
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=2)
    yield p
    await p.close()


async def _create_running_execution(pool: asyncpg.Pool, user_id: uuid.UUID) -> int:
    strategy_id = f"order-svc-test-{uuid.uuid4().hex[:8]}"
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO strategies
                (strategy_id, version, owner_user_id, target_asset, market, exchange,
                 fsm_definition, author_agent, lifecycle_status)
            VALUES ($1, '1.0.0', $2, 'BTC/USDT', 'crypto', 'bitget', $3::jsonb,
                    'test-author', 'APPROVED')
            """,
            strategy_id,
            user_id,
            json.dumps({}),
        )
        row = await conn.fetchrow(
            """
            INSERT INTO strategy_executions
                (strategy_id, strategy_version, user_id, exchange, mode,
                 allocated_capital, currency, status)
            VALUES ($1, '1.0.0', $2, 'bitget', 'PAPER', 100, 'USDT', 'RUNNING')
            RETURNING id
            """,
            strategy_id,
            user_id,
        )
    return row["id"]


def _market_order(execution_id: int, *, client_order_id: str | None = None) -> Order:
    return Order(
        client_order_id=client_order_id or f"test-{uuid.uuid4().hex}",
        strategy_id="strat-1",
        strategy_version="1.0.0",
        execution_id=execution_id,
        symbol="BTC/USDT",
        exchange="bitget",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("0.01"),
        asset_class=AssetClass.CRYPTO,
    )


async def test_submit_order_persists_and_publishes(pool):
    user_id = await create_test_user(pool)
    execution_id = await _create_running_execution(pool, user_id)
    adapter = FakeExchangeAdapter()
    published: list[tuple[str, dict]] = []

    async def publish(topic: str, payload: dict) -> None:
        published.append((topic, payload))

    order = _market_order(execution_id)
    result = await submit_order(order, user_id=user_id, adapter=adapter, pool=pool, publish=publish)

    assert result.status == OrderStatus.SUBMITTED
    assert result.exchange_order_id is not None
    assert adapter.place_order_call_count == 1
    assert published[0][0] == "order.status.changed"

    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM orders WHERE order_id = $1", result.order_id)
    assert row is not None
    assert row["status"] == "SUBMITTED"


async def test_submit_order_idempotent_on_same_client_order_id(pool):
    """FD-4.2-a 완료조건 — 동일 client_order_id로 2번 호출해도 실제 거래소
    호출은 1번만 발생해야 한다."""
    user_id = await create_test_user(pool)
    execution_id = await _create_running_execution(pool, user_id)
    adapter = FakeExchangeAdapter()

    order = _market_order(execution_id, client_order_id=f"idempotent-key-{uuid.uuid4().hex}")
    first = await submit_order(order, user_id=user_id, adapter=adapter, pool=pool)
    second = await submit_order(order, user_id=user_id, adapter=adapter, pool=pool)

    assert adapter.place_order_call_count == 1
    assert first.order_id == second.order_id


async def test_submit_order_concurrent_calls_only_send_to_exchange_once(pool):
    """레드팀 #2026-09-02-19 회귀 테스트 — 이전엔 "SELECT로 없음 확인 →
    거래소 전송 → INSERT" 순서라 동시 호출 둘 다 SELECT를 통과해 거래소에
    실제 주문을 두 번 낼 수 있었다. 지금은 거래소 호출 전에 INSERT로
    client_order_id를 먼저 원자적으로 선점하므로, 동시에 호출해도 실제
    place_order()는 정확히 1번만 일어나야 한다."""
    user_id = await create_test_user(pool)
    execution_id = await _create_running_execution(pool, user_id)

    async def slow_place_order(order: Order) -> Order:
        # 두 호출이 모두 claim-insert를 마치고 거래소 호출 구간에 동시에
        # 들어와 있을 시간을 인위적으로 벌어준다(경합 창을 넓힘).
        await asyncio.sleep(0.05)
        return order.model_copy(
            update={"exchange_order_id": f"ex-{uuid.uuid4()}", "status": OrderStatus.SUBMITTED}
        )

    adapter = FakeExchangeAdapter(on_place_order=slow_place_order)
    client_order_id = f"concurrent-key-{uuid.uuid4().hex}"
    order_a = _market_order(execution_id, client_order_id=client_order_id)
    order_b = _market_order(execution_id, client_order_id=client_order_id)

    results = await asyncio.gather(
        submit_order(order_a, user_id=user_id, adapter=adapter, pool=pool),
        submit_order(order_b, user_id=user_id, adapter=adapter, pool=pool),
    )

    assert adapter.place_order_call_count == 1
    assert results[0].order_id == results[1].order_id


async def test_submit_order_rejected_is_not_an_exception(pool):
    """FD-4.2-b 예외상황 — 거래소 REJECTED는 예외가 아니라 정상 흐름으로
    status=REJECTED 처리된다."""
    user_id = await create_test_user(pool)
    execution_id = await _create_running_execution(pool, user_id)
    adapter = FakeExchangeAdapter(place_order_result_status=OrderStatus.REJECTED)

    order = _market_order(execution_id)
    result = await submit_order(order, user_id=user_id, adapter=adapter, pool=pool)

    assert result.status == OrderStatus.REJECTED


async def test_submit_order_network_error_propagates(pool):
    """FD-4.2-b 예외상황 — 네트워크 오류는 RetryableExchangeError로 전파,
    이 함수 내부에서 자체 재시도하지 않는다(재시도 전 반드시 멱등성
    확인부터 다시 거쳐야 하므로)."""
    user_id = await create_test_user(pool)
    execution_id = await _create_running_execution(pool, user_id)

    async def failing_place_order(order: Order) -> Order:
        raise RetryableExchangeError("네트워크 오류")

    adapter = FakeExchangeAdapter(on_place_order=failing_place_order)
    order = _market_order(execution_id)

    with pytest.raises(RetryableExchangeError):
        await submit_order(order, user_id=user_id, adapter=adapter, pool=pool)

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM orders WHERE client_order_id = $1", order.client_order_id
        )
    assert row is None  # 전송 실패 — DB에 남지 않아야 함(반쯤 걸친 상태 방지)


async def test_update_from_exchange_raises_on_status_mismatch(pool):
    """레드팀 #2026-09-02-20 회귀 테스트 — 갱신 시점에 실제 DB의 status가
    호출자가 읽었던 값과 다르면(다른 경로가 먼저 바꿈) 조용히 덮어쓰지
    않고 ConcurrencyConflictError를 던져야 한다."""
    user_id = await create_test_user(pool)
    execution_id = await _create_running_execution(pool, user_id)
    adapter = FakeExchangeAdapter()
    order = _market_order(execution_id)
    submitted = await submit_order(order, user_id=user_id, adapter=adapter, pool=pool)
    assert submitted.status == OrderStatus.SUBMITTED

    # 다른 경로가 먼저 CANCELLED로 바꿨다고 가정 — 이 시점에 apply_fill이
    # (여전히 SUBMITTED인 줄 알고) FILLED로 덮어쓰려 하면 충돌해야 한다.
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE orders SET status = 'CANCELLED' WHERE order_id = $1", submitted.order_id
        )

    stale_update = submitted.model_copy(update={"status": OrderStatus.FILLED})
    async with pool.acquire() as conn:
        with pytest.raises(ConcurrencyConflictError):
            await order_repository.update_from_exchange(
                conn, stale_update, expected_status=OrderStatus.SUBMITTED
            )

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status FROM orders WHERE order_id = $1", submitted.order_id
        )
    assert row["status"] == "CANCELLED"  # 충돌한 쓰기는 반영되지 않았어야 함


async def test_cancel_order_updates_status(pool):
    user_id = await create_test_user(pool)
    execution_id = await _create_running_execution(pool, user_id)
    adapter = FakeExchangeAdapter()
    order = _market_order(execution_id)
    submitted = await submit_order(order, user_id=user_id, adapter=adapter, pool=pool)

    cancelled = await cancel_order(submitted.order_id, adapter=adapter, pool=pool)

    assert cancelled.status == OrderStatus.CANCELLED


async def test_cancel_nonexistent_order_raises(pool):
    adapter = FakeExchangeAdapter()
    with pytest.raises(OrderCancelError):
        await cancel_order(uuid.uuid4(), adapter=adapter, pool=pool)


async def test_cancel_already_filled_order_returns_unchanged_not_error(pool):
    """FD-4.3 예외상황 — 이미 체결된 주문의 취소 시도는 오류가 아니라
    상태 재조회로 전환(여기서는 취소 실패를 그대로 알려주는 것까지만
    확인 — FD-3.4 재조회 자체는 별도 호출부 책임)."""
    user_id = await create_test_user(pool)
    execution_id = await _create_running_execution(pool, user_id)
    adapter = FakeExchangeAdapter(cancel_result=False)
    order = _market_order(execution_id)
    submitted = await submit_order(order, user_id=user_id, adapter=adapter, pool=pool)

    result = await cancel_order(submitted.order_id, adapter=adapter, pool=pool)

    assert result.status == submitted.status  # 취소 실패 — 상태 그대로


async def test_modify_market_order_rejected_before_exchange_call(pool):
    user_id = await create_test_user(pool)
    execution_id = await _create_running_execution(pool, user_id)
    adapter = FakeExchangeAdapter()
    order = _market_order(execution_id)
    submitted = await submit_order(order, user_id=user_id, adapter=adapter, pool=pool)

    with pytest.raises(OrderModifyError):
        await modify_order(
            submitted.order_id,
            new_price=Decimal("50000"),
            new_quantity=Decimal("0.02"),
            adapter=adapter,
            pool=pool,
        )


async def test_resolve_unknown_confirms_status_within_max_attempts(pool):
    user_id = await create_test_user(pool)
    execution_id = await _create_running_execution(pool, user_id)
    adapter = FakeExchangeAdapter(get_order_status=OrderStatus.FILLED)
    order = _market_order(execution_id)
    submitted = await submit_order(order, user_id=user_id, adapter=adapter, pool=pool)

    sleep_calls = []

    async def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    resolved = await resolve_unknown(
        submitted.order_id, adapter=adapter, pool=pool, sleep=fake_sleep
    )

    assert resolved.status == OrderStatus.FILLED
    assert sleep_calls == []  # 1회차에 바로 확정 — 재시도 대기 없음


async def test_resolve_unknown_gives_up_after_max_attempts(pool):
    """FD-4.5 완료조건 — 강제 UNKNOWN 시뮬레이션 시 정확히 3회 재조회 후
    UNKNOWN을 유지하고 CRITICAL 로그를 남긴다(예외를 던지지 않음)."""
    user_id = await create_test_user(pool)
    execution_id = await _create_running_execution(pool, user_id)
    adapter = FakeExchangeAdapter(get_order_status=OrderStatus.UNKNOWN)
    order = _market_order(execution_id)
    submitted = await submit_order(order, user_id=user_id, adapter=adapter, pool=pool)
    # FD-4.5는 "주문 상태가 UNKNOWN으로 관측될 때"(예: FD-3.4 폴링 자체가
    # 실패) 트리거된다 — 여기서는 그 관측이 이미 일어나 DB에 UNKNOWN으로
    # 반영된 상태를 직접 시뮬레이션한다.
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE orders SET status = 'UNKNOWN' WHERE order_id = $1", submitted.order_id
        )

    sleep_calls = []

    async def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    resolved = await resolve_unknown(
        submitted.order_id, adapter=adapter, pool=pool, sleep=fake_sleep
    )

    assert resolved.status == OrderStatus.UNKNOWN
    assert sleep_calls == [2.0, 2.0]  # 3회 시도 중 마지막을 제외한 2회만 대기
