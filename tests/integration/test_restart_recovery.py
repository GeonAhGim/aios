"""05번 §5.6 재시작 복구 배선 통합테스트 — 실제 Postgres.

전수감사(docs/FULL_AUDIT_2026-09-02.md §3) 회귀: recover_pending_orders는
구현돼 있었으나 호출자가 없었다. 이 테스트는 실제 orders 행을 거래소 재조회
결과로 복구하고 이벤트를 재발행하며 audit_log에 남기는 배선을 검증한다.
"""

from __future__ import annotations

import json
import uuid
from decimal import Decimal
from pathlib import Path

import asyncpg
import pytest
from dotenv import dotenv_values

from src.data.models.base import AssetClass
from src.data.models.trading import Order, OrderSide, OrderStatus, OrderType
from src.exchanges.common.adapter import ExchangeAdapter
from src.services.credential_resolver import CredentialNotFoundError
from src.services.execution_loop.recovery_wiring import (
    RECOVERY_ACTION_TYPE,
    recover_orders_on_startup,
)
from src.services.order_service import repository
from tests.integration.conftest import create_test_user
from tests.integration.fake_exchange_adapter import FakeExchangeAdapter
from tests.integration.test_execution_tick import _create_execution


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


_UNSET = "__unset_exchange_order_id__"


async def _insert_order(
    pool: asyncpg.Pool,
    user_id: uuid.UUID,
    execution_id: int,
    *,
    status: OrderStatus = OrderStatus.SUBMITTED,
    exchange_order_id: str | None = _UNSET,
) -> Order:
    async with pool.acquire() as conn:
        execution = await conn.fetchrow(
            "SELECT strategy_id, strategy_version FROM strategy_executions WHERE id = $1",
            execution_id,
        )
        order = Order(
            client_order_id=f"recovery-{uuid.uuid4().hex}",
            exchange_order_id=(
                f"ex-{uuid.uuid4().hex[:12]}" if exchange_order_id is _UNSET else exchange_order_id
            ),
            strategy_id=execution["strategy_id"],
            strategy_version=execution["strategy_version"],
            execution_id=execution_id,
            symbol="BTC/USDT",
            exchange="bitget",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=Decimal("1"),
            status=status,
            asset_class=AssetClass.CRYPTO,
        )
        return await repository.insert(conn, order, user_id=user_id)


async def _insert_submitted_order(
    pool: asyncpg.Pool, user_id: uuid.UUID, execution_id: int
) -> Order:
    return await _insert_order(pool, user_id, execution_id, status=OrderStatus.SUBMITTED)


def _resolver_for(adapters: dict[uuid.UUID, ExchangeAdapter]):
    async def resolve(user_id: uuid.UUID, exchange: str) -> ExchangeAdapter:
        try:
            return adapters[user_id]
        except KeyError as exc:
            raise CredentialNotFoundError("자격증명 없음(테스트 리졸버)") from exc

    return resolve


async def _order_status(pool: asyncpg.Pool, order_id: uuid.UUID) -> str:
    async with pool.acquire() as conn:
        return await conn.fetchval("SELECT status FROM orders WHERE order_id = $1", order_id)


async def test_recovery_persists_cancelled_status_and_republishes(pool):
    user = await create_test_user(pool)
    execution_id = await _create_execution(pool, user)
    order = await _insert_submitted_order(pool, user, execution_id)
    adapter = FakeExchangeAdapter(get_order_status=OrderStatus.CANCELLED)
    published: list[tuple[str, dict]] = []

    async def publish(topic: str, payload: dict) -> None:
        published.append((topic, payload))

    recovered = await recover_orders_on_startup(
        pool, resolve_adapter=_resolver_for({user: adapter}), publish=publish
    )

    assert recovered >= 1
    assert await _order_status(pool, order.order_id) == "CANCELLED"
    mine = [
        p
        for t, p in published
        if t == "order.status.changed" and p["order_id"] == str(order.order_id)
    ]
    assert len(mine) == 1
    assert mine[0]["status"] == "CANCELLED"
    assert mine[0]["recovered"] is True
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT decision_data FROM audit_log WHERE action_type = $1", RECOVERY_ACTION_TYPE
        )
    assert rows
    assert any("recovered_orders" in json.loads(r["decision_data"]) for r in rows)


async def test_recovery_leaves_filled_order_for_the_tick_to_apply(pool):
    """FILLED는 tick의 apply_fill + FSM 전이 경로가 유일한 반영 지점이다 —
    복구가 먼저 FILLED로 쓰면 실행이 PENDING에 갇힌다."""
    user = await create_test_user(pool)
    execution_id = await _create_execution(pool, user)
    order = await _insert_submitted_order(pool, user, execution_id)
    adapter = FakeExchangeAdapter(get_order_status=OrderStatus.FILLED)
    published: list[tuple[str, dict]] = []

    async def publish(topic: str, payload: dict) -> None:
        published.append((topic, payload))

    await recover_orders_on_startup(
        pool, resolve_adapter=_resolver_for({user: adapter}), publish=publish
    )

    assert await _order_status(pool, order.order_id) == "SUBMITTED"
    mine = [p for t, p in published if p.get("order_id") == str(order.order_id)]
    assert len(mine) == 1
    assert mine[0]["status"] == "SUBMITTED"
    assert mine[0]["exchange_status"] == "FILLED"


async def test_recovery_skips_orders_without_credentials(pool):
    user = await create_test_user(pool)
    execution_id = await _create_execution(pool, user)
    order = await _insert_submitted_order(pool, user, execution_id)
    published: list[tuple[str, dict]] = []

    async def publish(topic: str, payload: dict) -> None:
        published.append((topic, payload))

    await recover_orders_on_startup(pool, resolve_adapter=_resolver_for({}), publish=publish)

    assert await _order_status(pool, order.order_id) == "SUBMITTED"
    assert not [p for _, p in published if p.get("order_id") == str(order.order_id)]


async def test_recovery_excludes_orders_without_exchange_order_id(pool):
    """불변식: exchange_order_id가 없는 주문은 거래소에 재조회할 대상이 없다
    (아직 거래소가 접수를 확인하지 않았을 수 있음) — 쿼리가 이를 걸러내야
    하고, 걸러진 주문은 재조회/재발행 대상이 되면 안 된다."""
    user = await create_test_user(pool)
    execution_id = await _create_execution(pool, user)
    order = await _insert_order(
        pool, user, execution_id, status=OrderStatus.SUBMITTED, exchange_order_id=None
    )
    adapter = FakeExchangeAdapter(get_order_status=OrderStatus.CANCELLED)
    published: list[tuple[str, dict]] = []

    async def publish(topic: str, payload: dict) -> None:
        published.append((topic, payload))

    await recover_orders_on_startup(
        pool, resolve_adapter=_resolver_for({user: adapter}), publish=publish
    )

    assert await _order_status(pool, order.order_id) == "SUBMITTED"
    assert not [p for _, p in published if p.get("order_id") == str(order.order_id)]


async def test_recovery_excludes_already_terminal_orders(pool):
    """불변식: 이미 최종 상태(FILLED)인 주문은 복구 대상이 아니다 — 재시작
    복구가 최종 상태를 재조회/재발행하면 이미 종료된 주문에 대해 중복 이벤트가
    나가거나 tick 경로와 경쟁하게 된다."""
    user = await create_test_user(pool)
    execution_id = await _create_execution(pool, user)
    order = await _insert_order(pool, user, execution_id, status=OrderStatus.FILLED)
    adapter = FakeExchangeAdapter(get_order_status=OrderStatus.CANCELLED)
    published: list[tuple[str, dict]] = []

    async def publish(topic: str, payload: dict) -> None:
        published.append((topic, payload))

    await recover_orders_on_startup(
        pool, resolve_adapter=_resolver_for({user: adapter}), publish=publish
    )

    assert await _order_status(pool, order.order_id) == "FILLED"
    assert not [p for _, p in published if p.get("order_id") == str(order.order_id)]


async def test_recovery_rejects_stale_write_on_concurrent_status_change(pool, monkeypatch):
    """실패주입: get_order_status 콜백이 DB에서 fresh read한 *이후*, 다른
    경로(tick.py 등)가 먼저 같은 주문을 갱신해 이제 conditional_update의
    expected_status가 어긋나는 상황을 재현한다 — `update_from_exchange`는
    `ConcurrencyConflictError`를 던져야 하고(레드팀 #2026-09-02-20), 복구
    루프는 그 주문 하나만 건너뛰며(§5.6 recover_pending_orders의 per-order
    try/except) 다른 주문과 전체 실행에는 영향을 주지 않아야 한다."""
    user = await create_test_user(pool)
    execution_id = await _create_execution(pool, user)
    stale_order = await _insert_submitted_order(pool, user, execution_id)
    other_order = await _insert_submitted_order(pool, user, execution_id)
    adapter = FakeExchangeAdapter(get_order_status=OrderStatus.CANCELLED)
    published: list[tuple[str, dict]] = []

    async def publish(topic: str, payload: dict) -> None:
        published.append((topic, payload))

    real_get_by_order_id = repository.get_by_order_id

    async def racing_get_by_order_id(conn, order_id):
        current = await real_get_by_order_id(conn, order_id)
        if current is not None and order_id == stale_order.order_id:
            await conn.execute(
                "UPDATE orders SET status = 'ACKNOWLEDGED' WHERE order_id = $1", order_id
            )
        return current

    monkeypatch.setattr(repository, "get_by_order_id", racing_get_by_order_id)

    await recover_orders_on_startup(
        pool, resolve_adapter=_resolver_for({user: adapter}), publish=publish
    )

    assert await _order_status(pool, stale_order.order_id) == "ACKNOWLEDGED"
    assert await _order_status(pool, other_order.order_id) == "CANCELLED"
    stale_events = [p for _, p in published if p.get("order_id") == str(stale_order.order_id)]
    assert not stale_events
    other_events = [p for _, p in published if p.get("order_id") == str(other_order.order_id)]
    assert len(other_events) == 1
    assert other_events[0]["status"] == "CANCELLED"


async def test_recovery_skips_order_when_adapter_raises_unexpected_error(pool):
    """실패주입: 거래소 어댑터 호출이 자격증명 문제가 아닌 예기치 못한
    예외(네트워크 오류 등)로 실패해도 recover_pending_orders의 per-order
    try/except가 그 주문만 건너뛰고 다른 주문과 audit_log 기록은 계속
    진행해야 한다(fail-closed이되 전체 복구가 죽지 않아야 함)."""
    user = await create_test_user(pool)
    execution_id = await _create_execution(pool, user)
    broken_order = await _insert_submitted_order(pool, user, execution_id)
    healthy_order = await _insert_submitted_order(pool, user, execution_id)

    class PartiallyExplodingAdapter(FakeExchangeAdapter):
        """`broken_order`의 exchange_order_id로 조회하면 터지고, 그 외는
        정상 응답한다 — 같은 user의 다른 주문은 영향받지 않음을 검증하기
        위해 어댑터가 아니라 조회 대상으로 실패를 구분한다."""

        async def get_order(self, order_id: str) -> Order:
            if order_id == broken_order.exchange_order_id:
                raise ConnectionError("거래소 응답 없음(테스트 실패주입)")
            return await super().get_order(order_id)

    adapter = PartiallyExplodingAdapter(get_order_status=OrderStatus.CANCELLED)
    published: list[tuple[str, dict]] = []

    async def publish(topic: str, payload: dict) -> None:
        published.append((topic, payload))

    await recover_orders_on_startup(
        pool, resolve_adapter=_resolver_for({user: adapter}), publish=publish
    )

    assert await _order_status(pool, broken_order.order_id) == "SUBMITTED"
    assert await _order_status(pool, healthy_order.order_id) == "CANCELLED"
    assert not [p for _, p in published if p.get("order_id") == str(broken_order.order_id)]
    healthy_events = [p for _, p in published if p.get("order_id") == str(healthy_order.order_id)]
    assert len(healthy_events) == 1
    assert healthy_events[0]["status"] == "CANCELLED"
