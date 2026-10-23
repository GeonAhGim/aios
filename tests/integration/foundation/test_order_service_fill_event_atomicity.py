"""task-16630 — FD-4.2-c 동기 체결(FILLED) 응답 경로의 원자성 회귀 테스트.

golden_red(backend_e2e) 조사(task-15397 후속)에서 발견: 거래소가
`place_order` 응답에서 즉시 FILLED를 돌려주는 동기체결 분기가
`orders.status`만 갱신하고 `order_events`/`fills`를 전혀 남기지 않았다
(sentinel: orders residue) — `replay_verify`로 재생하면 이벤트 이력이
비어 폴드 결과가 CREATED에 머물러 실제 상태(FILLED)와 불일치했다.

`repository.update_from_exchange_with_fill_event`가 상태 UPDATE +
`order_events`/`fills` INSERT를 같은 트랜잭션에서 원자적으로 커밋하도록
고친 뒤, 이 파일이 그 보장을 검증한다.
"""

from __future__ import annotations

import json
import uuid
from decimal import Decimal

import asyncpg
import pytest

from src.data.models.base import AssetClass, Currency, Money
from src.data.models.trading import Order, OrderSide, OrderStatus, OrderType
from src.services.order_service import repository as order_repository
from src.services.order_service import submit_order
from src.services.order_service.gate import GateDecision, GateOutcome
from tests.integration.conftest import create_test_tenant
from tests.integration.fake_exchange_adapter import FakeExchangeAdapter
from tests.support.order_service_isolation import isolated_order_service_pool


async def _allow_gate(context: object) -> GateDecision:
    return GateDecision(
        outcome=GateOutcome.ALLOW, decision_id=uuid.uuid4(), compliance_decision_id=uuid.uuid4()
    )


@pytest.fixture
async def pool():
    async with isolated_order_service_pool() as isolated:
        yield isolated


async def _create_running_execution(pool: asyncpg.Pool, user_id: uuid.UUID) -> int:
    strategy_id = f"order-svc-fill-event-{uuid.uuid4().hex[:8]}"
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


async def test_sync_fill_persists_order_events_and_fills_atomically(pool):
    """DoD(1) — 동기 FILLED 응답이 orders UPDATE와 order_events/fills INSERT를
    같은 트랜잭션에서 원자적으로 커밋한다."""
    user_id = await create_test_tenant(pool)
    execution_id = await _create_running_execution(pool, user_id)
    adapter = FakeExchangeAdapter(place_order_result_status=OrderStatus.FILLED)

    order = _market_order(execution_id)
    result = await submit_order(
        order, user_id=user_id, adapter=adapter, pool=pool, pre_submit_gate=_allow_gate
    )

    assert result.status == OrderStatus.FILLED
    assert result.filled_quantity == order.quantity

    async with pool.acquire() as conn:
        order_row = await conn.fetchrow("SELECT * FROM orders WHERE order_id = $1", result.order_id)
        event_rows = await conn.fetch(
            "SELECT * FROM order_events WHERE order_id = $1 ORDER BY seq", result.order_id
        )
        fill_rows = await conn.fetch("SELECT * FROM fills WHERE order_id = $1", result.order_id)

    assert order_row["status"] == "FILLED"
    assert len(event_rows) == 1
    assert event_rows[0]["from_status"] == "CREATED"
    assert event_rows[0]["to_status"] == "FILLED"
    assert event_rows[0]["event"] == "FILL"
    assert len(fill_rows) == 1
    assert fill_rows[0]["quantity"] == order.quantity


async def test_sync_fill_replay_folds_to_filled_not_created(pool):
    """DoD(1)/(3)의 핵심 — order_events 이력만으로 상태를 접으면(replay의
    핵심 전제) CREATED가 아니라 실제 상태(FILLED)에 도달해야 한다. 이 전엔
    이벤트가 비어 폴드 결과가 CREATED에 머무는 것이 sentinel(orders
    residue)이었다."""
    user_id = await create_test_tenant(pool)
    execution_id = await _create_running_execution(pool, user_id)
    adapter = FakeExchangeAdapter(place_order_result_status=OrderStatus.FILLED)

    order = _market_order(execution_id)
    result = await submit_order(
        order, user_id=user_id, adapter=adapter, pool=pool, pre_submit_gate=_allow_gate
    )

    async with pool.acquire() as conn:
        event_rows = await conn.fetch(
            "SELECT from_status, to_status FROM order_events WHERE order_id = $1 ORDER BY seq",
            result.order_id,
        )

    assert len(event_rows) > 0, "이벤트 이력이 비면 replay 폴드가 CREATED에 머문다(sentinel)"
    folded_status = event_rows[-1]["to_status"]
    assert folded_status == "FILLED" == result.status


async def test_sync_fill_event_write_failure_rolls_back_status_update(pool, monkeypatch):
    """DoD(2) negative test — order_events 기록이 실패하면 orders.status
    갱신도 같은 트랜잭션이라 함께 롤백돼야 한다(CREATED에 남는다). 상태만
    먼저 바뀌고 이벤트가 빠지는 절반-커밋 상태가 재발하지 않는지 확인."""
    user_id = await create_test_tenant(pool)
    execution_id = await _create_running_execution(pool, user_id)
    adapter = FakeExchangeAdapter(place_order_result_status=OrderStatus.FILLED)

    async def _boom(conn, event):
        raise RuntimeError("injected order_events append failure")

    monkeypatch.setattr(order_repository._order_events, "append", _boom)

    order = _market_order(execution_id)
    with pytest.raises(RuntimeError, match="injected order_events append failure"):
        await submit_order(
            order, user_id=user_id, adapter=adapter, pool=pool, pre_submit_gate=_allow_gate
        )

    async with pool.acquire() as conn:
        order_row = await conn.fetchrow(
            "SELECT * FROM orders WHERE client_order_id = $1", order.client_order_id
        )
        event_rows = await conn.fetch(
            "SELECT * FROM order_events WHERE order_id = $1", order_row["order_id"]
        )
        fill_rows = await conn.fetch(
            "SELECT * FROM fills WHERE order_id = $1", order_row["order_id"]
        )

    assert order_row is not None
    assert order_row["status"] == "CREATED"  # 갱신 전체가 롤백됨 — FILLED로 남지 않는다
    assert len(event_rows) == 0
    assert len(fill_rows) == 0


async def test_sync_fill_without_exchange_order_id_fails_closed(pool):
    """DoD 보강 — fills.exchange_order_id는 NOT NULL 계약(FillEvent 스키마)
    이라, FILLED인데 exchange_order_id가 없는 응답은 추측해 채우지 않고
    명시적으로 거부해야 한다(8.3 원칙 — 모르는 상태를 성공으로 위장하지
    않는다)."""
    user_id = await create_test_tenant(pool)
    execution_id = await _create_running_execution(pool, user_id)

    async def _filled_without_exchange_order_id(order: Order) -> Order:
        return order.model_copy(
            update={
                "exchange_order_id": None,
                "status": OrderStatus.FILLED,
                "filled_quantity": order.quantity,
                "average_fill_price": Money(amount=Decimal("50000"), currency=Currency.USDT),
            }
        )

    adapter = FakeExchangeAdapter(on_place_order=_filled_without_exchange_order_id)
    order = _market_order(execution_id)

    with pytest.raises(ValueError, match="exchange_order_id"):
        await submit_order(
            order, user_id=user_id, adapter=adapter, pool=pool, pre_submit_gate=_allow_gate
        )

    async with pool.acquire() as conn:
        order_row = await conn.fetchrow(
            "SELECT * FROM orders WHERE client_order_id = $1", order.client_order_id
        )
    assert order_row["status"] == "CREATED"


async def test_sync_fill_replay_digest_matches_orders_row(pool):
    """DoD(3) -- the replay_verify pair (projection of order_events + fills vs the
    stored orders row) is byte-identical for a synchronously filled order,
    including version and the fee fields."""
    from scripts import replay_verify

    user_id = await create_test_tenant(pool)
    execution_id = await _create_running_execution(pool, user_id)
    adapter = FakeExchangeAdapter(place_order_result_status=OrderStatus.FILLED)

    order = _market_order(execution_id)
    result = await submit_order(
        order, user_id=user_id, adapter=adapter, pool=pool, pre_submit_gate=_allow_gate
    )

    async with pool.acquire() as conn:
        pair = await replay_verify._order_pair(conn, result.order_id, cutover_at=None)

    assert pair is not None
    replayed, actual = pair
    assert replayed == actual
    assert actual["status"] == "FILLED"
