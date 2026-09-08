"""L4-15 `application/inbox_processor.py` 실DB 통합테스트.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §9 L4-15
("1000회 중복 → fills 1행; tick의 `_handle_pending_fill_check`가 inbox
경유해도 FSM 전이 동일"), §4.2 FILL 행, §6 F9.

1000회 중복 자체(스톰 규모·3워커 동시성)는
`tests/adversarial/oms/test_duplicate_delivery_storm.py`가 다룬다 — 이
파일은 `InboxProcessor`의 정상 경로(부분→전량 체결, position_ledger 1회
반영)와 fail-closed negative(미지 주문·venue 불일치 위조 이벤트)를 다룬다.
tick의 `_handle_pending_fill_check` → `apply_fill` 경로가 inbox를 거쳐도
기존 FSM 전이가 동일함은 `tests/integration/test_execution_tick.py::
test_paused_execution_still_checks_pending_order_fill`(수정 없이 그대로
통과)가 이미 증명한다 — 이 파일의 `test_apply_fill_wrapper_delegates_to_inbox_processor`는
그 위임 자체(호환 래퍼가 실제로 inbox를 타는지)를 좁게 확인한다.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from src.data.models.base import Currency, Money
from src.data.models.trading import OrderSide, OrderStatus
from src.services.oms.adapters.inbox_repository import InboxRepository
from src.services.oms.application.inbox_processor import InboxProcessor
from src.services.oms.contracts.v1_events import FillEvent, ProviderOrderEvent
from src.services.order_service import repository as legacy_order_repository
from src.services.order_service.submit import apply_fill
from tests.integration.oms.conftest import create_test_tenant, create_test_user


async def _seed_execution(pool, user_id: UUID) -> int:
    strategy_id = f"oms-inbox-test-{uuid4().hex[:8]}"
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
    pool,
    user_id: UUID,
    *,
    execution_id: int | None = None,
    status: str = "SUBMITTED",
    quantity: Decimal = Decimal("1"),
    exchange: str = "bitget",
    exchange_order_id: str | None = None,
) -> tuple[UUID, str, str]:
    """`exchange_order_id`는 명시하지 않으면 매번 새로 생성한다 — 공유
    테스트 DB에 고정 문자열("ex-1" 등)을 반복 사용하면, `exchange_order_id`가
    UNIQUE 제약이 없는 컬럼이라 다른 (이전 실행의) 행과 우연히 겹쳐
    `InboxProcessor._resolve_order_id`가 엉뚱한 주문을 집을 수 있다."""
    client_order_id = f"cid-{uuid4().hex}"
    resolved_exchange_order_id = exchange_order_id or f"ex-{uuid4().hex}"
    async with pool.acquire() as conn:
        order_id = await conn.fetchval(
            """
            INSERT INTO orders (
                user_id, client_order_id, exchange_order_id, strategy_id, strategy_version,
                execution_id, symbol, exchange, side, order_type, quantity, status,
                filled_quantity, is_liquidation, asset_class
            ) VALUES ($1,$2,$3,'oms-inbox-test','1.0.0',$4,'BTC/USDT',$5,'BUY','MARKET',
                      $6,$7,0,false,'CRYPTO')
            RETURNING order_id
            """,
            user_id,
            client_order_id,
            resolved_exchange_order_id,
            execution_id,
            exchange,
            quantity,
            status,
        )
    return order_id, client_order_id, resolved_exchange_order_id


def _fill_event(
    *,
    venue: str = "bitget",
    exchange_order_id: str,
    client_order_id: str,
    quantity: Decimal,
    price: Decimal = Decimal("100"),
) -> ProviderOrderEvent:
    fill_id = f"fill-{uuid4().hex}"
    now = datetime.now(timezone.utc)
    fill = FillEvent(
        provider_fill_id=fill_id,
        venue=venue,
        order_id=None,
        exchange_order_id=exchange_order_id,
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        quantity=quantity,
        price=price,
        fee=Decimal("0"),
        fee_currency="USDT",
        liquidity="TAKER",
        venue_ts=now,
    )
    return ProviderOrderEvent(
        provider_event_id=fill_id,
        venue=venue,
        venue_symbol="BTCUSDT",
        exchange_order_id=exchange_order_id,
        client_order_id=client_order_id,
        venue_status="FILLED",
        filled_quantity=quantity,
        average_price=price,
        last_fill=fill,
        venue_ts=now,
        received_at=now,
        source="WS",
        raw_hash=hashlib.sha256(fill_id.encode()).hexdigest(),
    )


async def test_ingest_full_fill_transitions_to_filled_and_updates_position_once(pool):
    """position_ledger가 `strategy_executions.user_id`를 `pos_account.tenant_id`로
    그대로 쓴다 — 대응 `tenant` 행이 있어야 하므로 `create_test_tenant()`가 필요하다
    (다른 테스트는 execution_id를 안 넘겨 이 경로를 안 타 create_test_user()로 충분)."""
    user_id = await create_test_tenant(pool)
    execution_id = await _seed_execution(pool, user_id)
    order_id, client_order_id, exchange_order_id = await _insert_order(
        pool, user_id, execution_id=execution_id, quantity=Decimal("2")
    )
    ev = _fill_event(
        client_order_id=client_order_id, exchange_order_id=exchange_order_id, quantity=Decimal("2")
    )
    processor = InboxProcessor(pool)

    result = await processor.ingest(ev)

    print(f"ingest full fill: ingested={result}")
    assert result is True
    async with pool.acquire() as conn:
        order = await conn.fetchrow(
            "SELECT status, filled_quantity, average_fill_price FROM orders WHERE order_id = $1",
            order_id,
        )
        fills_count = await conn.fetchval(
            "SELECT count(*) FROM fills WHERE order_id = $1", order_id
        )
        position_qty = await conn.fetchval(
            "SELECT quantity FROM positions WHERE execution_id = $1", execution_id
        )
    assert order["status"] == "FILLED"
    assert order["filled_quantity"] == Decimal("2")
    assert order["average_fill_price"] == Decimal("100")
    assert fills_count == 1
    assert position_qty == Decimal("2")  # LB-12 position_ledger 위임 — 1회 반영


async def test_ingest_partial_then_full_fill_matches_state_machine_semantics(pool):
    user_id = await create_test_user(pool)
    order_id, client_order_id, exchange_order_id = await _insert_order(
        pool, user_id, quantity=Decimal("10")
    )
    processor = InboxProcessor(pool)

    first = _fill_event(
        client_order_id=client_order_id, exchange_order_id=exchange_order_id, quantity=Decimal("4")
    )
    assert await processor.ingest(first) is True
    async with pool.acquire() as conn:
        status_after_partial = await conn.fetchval(
            "SELECT status FROM orders WHERE order_id = $1", order_id
        )
    assert status_after_partial == "PARTIALLY_FILLED"

    second = _fill_event(
        client_order_id=client_order_id, exchange_order_id=exchange_order_id, quantity=Decimal("6")
    )
    assert await processor.ingest(second) is True
    async with pool.acquire() as conn:
        order = await conn.fetchrow(
            "SELECT status, filled_quantity FROM orders WHERE order_id = $1", order_id
        )
        fills_count = await conn.fetchval(
            "SELECT count(*) FROM fills WHERE order_id = $1", order_id
        )
    assert order["status"] == "FILLED"
    assert order["filled_quantity"] == Decimal("10")
    assert fills_count == 2


async def test_ingest_duplicate_event_absorbed_only_one_fill_row(pool):
    user_id = await create_test_user(pool)
    order_id, client_order_id, exchange_order_id = await _insert_order(
        pool, user_id, quantity=Decimal("1")
    )
    ev = _fill_event(
        client_order_id=client_order_id, exchange_order_id=exchange_order_id, quantity=Decimal("1")
    )
    processor = InboxProcessor(pool)

    results = [await processor.ingest(ev) for _ in range(5)]

    print(f"duplicate ingest results={results}")
    assert results == [True, False, False, False, False]
    async with pool.acquire() as conn:
        fills_count = await conn.fetchval(
            "SELECT count(*) FROM fills WHERE order_id = $1", order_id
        )
        status = await conn.fetchval("SELECT status FROM orders WHERE order_id = $1", order_id)
    assert fills_count == 1
    assert status == "FILLED"


async def test_ingest_unknown_order_is_ignored_fail_closed(pool):
    """negative(1/2) — 매칭되는 주문이 아예 없는 이벤트는 조용히 IGNORED."""
    ev = _fill_event(
        client_order_id="cid-does-not-exist",
        exchange_order_id="ex-does-not-exist",
        quantity=Decimal("1"),
    )
    processor = InboxProcessor(pool)

    result = await processor.ingest(ev)

    assert result is True  # 이벤트 자체는 신규 삽입(중복 아님) — 다만 미적용
    async with pool.acquire() as conn:
        state = await conn.fetchval(
            "SELECT state FROM provider_event_inbox WHERE provider_event_id = $1",
            ev.provider_event_id,
        )
        fills_count = await conn.fetchval(
            "SELECT count(*) FROM fills WHERE exchange_order_id = $1", "ex-does-not-exist"
        )
    assert state == "IGNORED"
    assert fills_count == 0


async def test_ingest_venue_mismatch_forged_event_is_ignored_fail_closed(pool):
    """negative(2/2) — 테넌트/거래소 불일치 위조 이벤트. 실제 소유 거래소가
    다른 order의 client_order_id를 참조해도(venue 다름) 적용되지 않는다."""
    user_id = await create_test_user(pool)
    order_id, client_order_id, exchange_order_id = await _insert_order(
        pool, user_id, quantity=Decimal("1"), exchange="bitget"
    )
    forged = _fill_event(
        venue="kis", client_order_id=client_order_id, exchange_order_id=exchange_order_id,
        quantity=Decimal("1"),
    )
    processor = InboxProcessor(pool)

    result = await processor.ingest(forged)

    assert result is True
    async with pool.acquire() as conn:
        order = await conn.fetchrow(
            "SELECT status, filled_quantity FROM orders WHERE order_id = $1", order_id
        )
        fills_count = await conn.fetchval(
            "SELECT count(*) FROM fills WHERE order_id = $1", order_id
        )
        inbox_state = await conn.fetchval(
            "SELECT state FROM provider_event_inbox WHERE provider_event_id = $1",
            forged.provider_event_id,
        )
    assert order["status"] == "SUBMITTED"  # 미적용 — 원래 상태 그대로
    assert order["filled_quantity"] == Decimal("0")
    assert fills_count == 0
    assert inbox_state == "IGNORED"


async def test_process_once_drains_backlog_row_via_skip_locked(pool):
    """`ingest()`가 아니라 직접 삽입만 된(예: RESYNC 백로그) 행도
    `process_once()`가 SKIP LOCKED로 집어 같은 처리 경로를 탄다."""
    user_id = await create_test_user(pool)
    order_id, client_order_id, exchange_order_id = await _insert_order(
        pool, user_id, quantity=Decimal("3")
    )
    ev = _fill_event(
        client_order_id=client_order_id, exchange_order_id=exchange_order_id, quantity=Decimal("3")
    )
    async with pool.acquire() as conn:
        assert await InboxRepository().insert_if_absent(conn, ev) is True
    processor = InboxProcessor(pool)

    # limit을 넉넉히 준다 — 공유 테스트 DB에는 다른 테스트 실행분이 남긴
    # 미처리 행이 섞여 있을 수 있다(test_inbox_fills_idempotency.py의 동일
    # 관례 참조). SKIP LOCKED는 오래된 행부터 집으므로, 내 행이 뒤에 있어도
    # 이 호출 안에서 결국 도달해야 한다.
    processed = await processor.process_once(limit=5000)

    print(f"process_once drained={processed}")
    assert processed >= 1
    async with pool.acquire() as conn:
        order = await conn.fetchrow(
            "SELECT status, filled_quantity FROM orders WHERE order_id = $1", order_id
        )
        state = await conn.fetchval(
            "SELECT state FROM provider_event_inbox WHERE provider_event_id = $1",
            ev.provider_event_id,
        )
    assert order["status"] == "FILLED"
    assert state == "PROCESSED"


async def test_apply_fill_wrapper_delegates_to_inbox_processor(pool):
    """DoD(2) — `order_service.submit.apply_fill`은 시그니처를 유지한 채
    실제 처리를 `InboxProcessor.ingest()`에 위임하는 래퍼로 축소됐다."""
    user_id = await create_test_user(pool)
    order_id, _client_order_id, exchange_order_id = await _insert_order(
        pool, user_id, quantity=Decimal("1")
    )
    async with pool.acquire() as conn:
        current = await legacy_order_repository.get_by_order_id(conn, order_id)
    assert current is not None

    updated = await apply_fill(
        current,
        exchange_order_id=exchange_order_id,
        filled_quantity=Decimal("1"),
        average_fill_price=Money(amount=Decimal("100"), currency=Currency.USDT),
        pool=pool,
    )

    assert updated.status == OrderStatus.FILLED
    async with pool.acquire() as conn:
        fills_count = await conn.fetchval(
            "SELECT count(*) FROM fills WHERE order_id = $1", order_id
        )
    assert fills_count == 1
