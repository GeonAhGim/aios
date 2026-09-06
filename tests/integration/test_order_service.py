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
from src.data.models.base import AssetClass, Currency, Money
from src.data.models.trading import Order, OrderSide, OrderStatus, OrderType
from src.services.oms.domain.venue_profile import TimeoutBudget, VenueCapabilityProfile
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
    확인부터 다시 거쳐야 하므로).

    task-1566(L4-09) 편차 — 이전엔 claim 행을 지워 DB에 흔적을 남기지
    않았다. 지금은 oms `order_repository.transition()`으로 CREATED→FAILED
    확정만 하고 행은 남긴다(감사 흔적 보존, order_events WORM에도 남음)."""
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
    assert row is not None  # 행은 남는다 — 삭제 대신 상태로 실패를 표현
    assert row["status"] in ("UNKNOWN", "FAILED")


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


async def test_cancel_order_acknowledged_enqueues_cancel_command(pool):
    """task-1603(L4-17) 편차 — `cancel_order`는 더 이상 거래소를 동기 호출하지
    않는다(`oms.application.cancel_order`에 위임, 모듈 docstring 참조). ACK된
    주문의 취소는 자기루프 전이 + outbox `CANCEL` enqueue로 끝나고, 실제
    CANCELLED 확정은 `outbox_dispatcher`(L4-14)/inbox(L4-15)가 비동기로 한다."""
    user_id = await create_test_user(pool)
    execution_id = await _create_running_execution(pool, user_id)
    adapter = FakeExchangeAdapter(place_order_result_status=OrderStatus.ACKNOWLEDGED)
    order = _market_order(execution_id)
    submitted = await submit_order(order, user_id=user_id, adapter=adapter, pool=pool)

    cancelled = await cancel_order(submitted.order_id, adapter=adapter, pool=pool)

    assert cancelled.status == OrderStatus.ACKNOWLEDGED  # 자기루프 — 상태 불변
    async with pool.acquire() as conn:
        outbox_row = await conn.fetchrow(
            "SELECT command_type FROM order_command_outbox WHERE order_id = $1",
            submitted.order_id,
        )
    assert outbox_row is not None
    assert outbox_row["command_type"] == "CANCEL"


async def test_cancel_nonexistent_order_raises(pool):
    adapter = FakeExchangeAdapter()
    with pytest.raises(OrderCancelError):
        await cancel_order(uuid.uuid4(), adapter=adapter, pool=pool)


async def test_cancel_already_filled_order_raises(pool):
    """FD-4.3 편차(task-1603/L4-17) — 이미 체결된 주문의 취소는 이제 조용한
    상태 재조회가 아니라 명시적 거부다(§4.2 전이표 밖, `oms.application.
    cancel_order`가 fail-closed로 `InvalidOrderTransitionError`를 던지고
    `OrderCancelError`로 감싸 전파한다)."""
    user_id = await create_test_user(pool)
    execution_id = await _create_running_execution(pool, user_id)
    adapter = FakeExchangeAdapter(place_order_result_status=OrderStatus.FILLED)
    order = _market_order(execution_id)
    submitted = await submit_order(order, user_id=user_id, adapter=adapter, pool=pool)
    assert submitted.status == OrderStatus.FILLED

    with pytest.raises(OrderCancelError):
        await cancel_order(submitted.order_id, adapter=adapter, pool=pool)


class _ModifiableFakeAdapter(FakeExchangeAdapter):
    """`FakeExchangeAdapter`는 L4-13 `venue_profile()` 기본 구현(미지원 예외,
    `exchanges/common/adapter.py` 참조)을 아직 오버라이드하지 않는다 — 이
    테스트 파일 전용으로만 `supports_modify=True` 프로파일을 얹어, MARKET
    주문 거부 검증(oms.application.modify_order)이 capability 단계가 아니라
    실제로 의도한 order_type 검증에서 걸리도록 한다."""

    def venue_profile(self) -> VenueCapabilityProfile:
        return VenueCapabilityProfile(
            venue="bitget",
            asset_classes=[AssetClass.CRYPTO],
            order_types={OrderType.MARKET, OrderType.LIMIT},
            time_in_force={"GTC", "IOC"},
            supports_client_order_id=True,
            client_order_id_max_len=40,
            client_order_id_charset="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
            id_policy="STABLE",
            supports_modify=True,
            supports_cancel="YES",
            supports_ws_orders=True,
            supports_batch=False,
            price_tick={},
            qty_lot={},
            min_notional={},
            rate_limits={},
            submit_timeout=TimeoutBudget(),
            query_timeout=TimeoutBudget(),
            market_hours=None,
            max_open_orders_per_symbol=20,
            verified="DOC_ONLY",
        )


async def test_modify_market_order_rejected_before_exchange_call(pool):
    user_id = await create_test_user(pool)
    execution_id = await _create_running_execution(pool, user_id)
    adapter = _ModifiableFakeAdapter(place_order_result_status=OrderStatus.ACKNOWLEDGED)
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


async def test_synchronous_fill_round_trip_opens_and_closes_position(pool):
    """PM 배정(agent-platform-12, 2026-09-02) 회귀 테스트 — positions
    테이블에 아무도 쓰지 않아 risk_guard_service/portfolio_service의
    PnL 합산이 항상 0이었던 결함. BUY 동기체결로 포지션이 열리고, SELL
    동기체결로 realized_pnl과 함께 닫히는지 확인."""
    user_id = await create_test_user(pool)
    execution_id = await _create_running_execution(pool, user_id)

    def _fill_at(price: str):
        async def on_place_order(order: Order) -> Order:
            return order.model_copy(
                update={
                    "exchange_order_id": f"ex-{uuid.uuid4()}",
                    "status": OrderStatus.FILLED,
                    "filled_quantity": order.quantity,
                    "average_fill_price": Money(amount=Decimal(price), currency=Currency.USDT),
                }
            )

        return on_place_order

    buy_adapter = FakeExchangeAdapter(on_place_order=_fill_at("50000"))
    buy_order = _market_order(execution_id)
    await submit_order(buy_order, user_id=user_id, adapter=buy_adapter, pool=pool)

    async with pool.acquire() as conn:
        opened = await conn.fetchrow(
            "SELECT * FROM positions WHERE execution_id = $1", execution_id
        )
    assert opened is not None
    assert opened["quantity"] == Decimal("0.0100000000")
    assert opened["average_entry_price"] == Decimal("50000.0000000000")
    assert opened["closed_at"] is None

    sell_adapter = FakeExchangeAdapter(on_place_order=_fill_at("55000"))
    sell_order = _market_order(execution_id).model_copy(
        update={"side": OrderSide.SELL, "client_order_id": f"sell-{uuid.uuid4().hex}"}
    )
    await submit_order(sell_order, user_id=user_id, adapter=sell_adapter, pool=pool)

    async with pool.acquire() as conn:
        closed = await conn.fetchrow(
            "SELECT * FROM positions WHERE id = $1", opened["id"]
        )
    assert closed["quantity"] == Decimal("0")
    assert closed["closed_at"] is not None
    # (55000 - 50000) * 0.01 = 50
    assert closed["realized_pnl"] == Decimal("50.0000000000")
