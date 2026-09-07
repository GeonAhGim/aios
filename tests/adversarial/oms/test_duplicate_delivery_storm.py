"""L4-15 DoD — 같은 체결 이벤트 1000회(동시 3워커) → fills 1행, 정확한 수량.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §8.3
`test_duplicate_delivery_storm.py`("같은 체결 이벤트 1000회 → fills 1행,
filled_quantity 정확"), §9 L4-15, §6 F9.

절대 지연(sleep 시간) 단언은 하지 않는다 — 구조(행 수·결과 목록) 단언과
print만(TESTING.md 관례, tests/adversarial/oms/test_concurrent_submit.py와
동일).

`InboxRepository.insert_if_absent`의 `ON CONFLICT (venue, provider_event_id)
DO NOTHING RETURNING`가 유일한 동시성 관문이다 — 3개의 `InboxProcessor`
인스턴스(워커 비유, 같은 pool 공유)에 1000개의 `ingest()` 호출을 고르게
나눠 `asyncio.gather`로 한꺼번에 던져도, Postgres UNIQUE 인덱스가 승자
하나만 커밋시키므로 `_process_row`(fills 삽입·state_machine 전이·
position_ledger 반영)는 정확히 한 번만 실행된다.
"""
from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from src.data.models.trading import OrderSide
from src.services.oms.application.inbox_processor import InboxProcessor
from src.services.oms.contracts.v1_events import FillEvent, ProviderOrderEvent
from tests.integration.oms.conftest import create_test_tenant

_STORM_SIZE = 1000
_WORKERS = 3


async def _seed_execution(pool, user_id: UUID) -> int:
    strategy_id = f"oms-storm-test-{uuid4().hex[:8]}"
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO strategies (
                strategy_id, version, owner_user_id, target_asset, market, exchange,
                fsm_definition, author_agent, lifecycle_status
            ) VALUES ($1, '1.0.0', $2, 'BTC/USDT', 'crypto', 'bitget', '{}'::jsonb,
                      'test-author', 'APPROVED')
            """,
            strategy_id,
            user_id,
        )
        execution_id = await conn.fetchval(
            """
            INSERT INTO strategy_executions (
                strategy_id, strategy_version, user_id, exchange, mode,
                allocated_capital, currency, status
            ) VALUES ($1, '1.0.0', $2, 'bitget', 'PAPER', 1000, 'USDT', 'RUNNING')
            RETURNING id
            """,
            strategy_id,
            user_id,
        )
    return int(execution_id)


async def _insert_order(
    pool, user_id: UUID, *, execution_id: int, quantity: Decimal, exchange_order_id: str
) -> tuple[UUID, str]:
    client_order_id = f"cid-{uuid4().hex}"
    async with pool.acquire() as conn:
        order_id = await conn.fetchval(
            """
            INSERT INTO orders (
                user_id, client_order_id, exchange_order_id, strategy_id, strategy_version,
                execution_id, symbol, exchange, side, order_type, quantity, status,
                filled_quantity, is_liquidation, asset_class
            ) VALUES ($1,$2,$3,'oms-storm-test','1.0.0',$4,'BTC/USDT','bitget','BUY',
                      'MARKET',$5,'SUBMITTED',0,false,'CRYPTO')
            RETURNING order_id
            """,
            user_id,
            client_order_id,
            exchange_order_id,
            execution_id,
            quantity,
        )
    return order_id, client_order_id


def _full_fill_event(
    *, client_order_id: str, quantity: Decimal, exchange_order_id: str = "ex-storm-1"
) -> ProviderOrderEvent:
    """스톰 전체가 재전송하는 **같은** 이벤트 — `provider_event_id`/
    `provider_fill_id`가 매 호출 동일해야 DoD의 "같은 체결 이벤트"가 된다."""
    fill_id = f"storm-fill-{uuid4().hex}"
    now = datetime.now(timezone.utc)
    fill = FillEvent(
        provider_fill_id=fill_id,
        venue="bitget",
        order_id=None,
        exchange_order_id=exchange_order_id,
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        quantity=quantity,
        price=Decimal("100"),
        fee=Decimal("0.01"),
        fee_currency="USDT",
        liquidity="TAKER",
        venue_ts=now,
    )
    return ProviderOrderEvent(
        provider_event_id=fill_id,
        venue="bitget",
        venue_symbol="BTCUSDT",
        exchange_order_id=exchange_order_id,
        client_order_id=client_order_id,
        venue_status="FILLED",
        filled_quantity=quantity,
        average_price=Decimal("100"),
        last_fill=fill,
        venue_ts=now,
        received_at=now,
        source="WS",
        raw_hash=hashlib.sha256(fill_id.encode()).hexdigest(),
    )


async def test_1000_duplicate_deliveries_across_3_workers_produce_exactly_one_fill(pool):
    user_id = await create_test_tenant(pool)
    execution_id = await _seed_execution(pool, user_id)
    quantity = Decimal("5")
    exchange_order_id = f"ex-storm-{uuid4().hex}"
    order_id, client_order_id = await _insert_order(
        pool, user_id, execution_id=execution_id, quantity=quantity,
        exchange_order_id=exchange_order_id,
    )
    ev = _full_fill_event(
        client_order_id=client_order_id, quantity=quantity, exchange_order_id=exchange_order_id,
    )
    workers = [InboxProcessor(pool) for _ in range(_WORKERS)]

    results = await asyncio.gather(
        *[workers[i % _WORKERS].ingest(ev) for i in range(_STORM_SIZE)]
    )

    print(f"duplicate_delivery_storm: size={_STORM_SIZE} workers={_WORKERS} "
          f"absorbed_true={sum(results)}")
    assert sum(results) == 1  # 승자 정확히 1명 — 나머지는 전부 흡수(F9)

    async with pool.acquire() as conn:
        fills_count = await conn.fetchval(
            "SELECT count(*) FROM fills WHERE order_id = $1", order_id
        )
        order = await conn.fetchrow(
            "SELECT status, filled_quantity FROM orders WHERE order_id = $1", order_id
        )
        position_qty = await conn.fetchval(
            "SELECT quantity FROM positions WHERE execution_id = $1", execution_id
        )
    assert fills_count == 1
    assert order["status"] == "FILLED"
    assert order["filled_quantity"] == quantity
    assert position_qty == quantity  # 잔고/포지션(LB-12 위임) 1회 반영


async def test_1000_duplicate_deliveries_of_unmatched_event_never_apply(pool):
    """위조/미매칭 이벤트가 스톰으로 와도(예: 재전송 폭주) 적용은 0회이고
    inbox 행도 1개로 수렴한다 — fail-closed 경로도 흡수가 그대로 작동한다."""
    exchange_order_id = f"ex-storm-unmatched-{uuid4().hex}"
    ev = _full_fill_event(
        client_order_id="cid-storm-unmatched", quantity=Decimal("1"),
        exchange_order_id=exchange_order_id,
    )
    workers = [InboxProcessor(pool) for _ in range(_WORKERS)]

    results = await asyncio.gather(
        *[workers[i % _WORKERS].ingest(ev) for i in range(_STORM_SIZE)]
    )

    print(f"duplicate_delivery_storm(unmatched): absorbed_true={sum(results)}")
    assert sum(results) == 1
    async with pool.acquire() as conn:
        inbox_rows = await conn.fetchval(
            "SELECT count(*) FROM provider_event_inbox WHERE provider_event_id = $1",
            ev.provider_event_id,
        )
        state = await conn.fetchval(
            "SELECT state FROM provider_event_inbox WHERE provider_event_id = $1",
            ev.provider_event_id,
        )
        fills_count = await conn.fetchval(
            "SELECT count(*) FROM fills WHERE exchange_order_id = $1", exchange_order_id
        )
    assert inbox_rows == 1
    assert state == "IGNORED"
    assert fills_count == 0
