"""L4-29(task-2180) 적대적 2/2 — 변조된 provider 체결 이벤트가 거부되는지 실증한다.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §9 L4-29(선행 L4-15
`application/inbox_processor.py`). "거부됐다"만 단언하지 않고 `fills`/`orders`/
`positions` 행 수를 수치로 확인한다 — mock 자기단언이 아니라 실 DB·실
`InboxProcessor` 경로(`test_duplicate_delivery_storm.py`와 동일 관례)를 탄다.

세 변조 벡터, 전부 `inbox_processor._process_row`의 fail-closed 분기가 실제로
막는 경로(모듈 docstring 그대로 — 이 두 검사는 예외를 던지지 않고 조용히
IGNORED/PROCESSED로만 남긴다, 위조 이벤트 하나가 배경 드레인 전체를 죽이면
안 되므로):
  (1) venue 위조 — 실제 주문은 bitget인데 이벤트가 다른 거래소(venue)를
      자칭한다. `order.exchange != ev.venue`에 걸려 적용되지 않는다.
  (2) 주문 참조 위조 — 존재하지 않는 client_order_id/exchange_order_id를
      담아 아무 주문에도 매칭되지 않는다.
  (3) 종결 후 재전송(이중지급 시도) — 이미 FILLED로 종결된 주문에 수량을
      부풀린 두 번째 체결 이벤트를 보낸다. `is_terminal(order.status)`가
      걸려 fills 삽입 자체가 일어나지 않는다.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from src.data.models.trading import OrderSide
from src.services.oms.application.inbox_processor import InboxProcessor
from src.services.oms.contracts.v1_events import FillEvent, ProviderOrderEvent
from tests.integration.oms.conftest import create_test_tenant


async def _seed_execution(pool, user_id: UUID) -> int:
    strategy_id = f"oms-tamper-test-{uuid4().hex[:8]}"
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
            ) VALUES ($1,$2,$3,'oms-tamper-test','1.0.0',$4,'BTC/USDT','bitget','BUY',
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


def _fill_event(
    *,
    venue: str,
    client_order_id: str | None,
    exchange_order_id: str | None,
    quantity: Decimal,
    provider_event_id: str,
) -> ProviderOrderEvent:
    now = datetime.now(timezone.utc)
    fill = FillEvent(
        provider_fill_id=provider_event_id,
        venue=venue,
        order_id=None,
        exchange_order_id=exchange_order_id or "unknown",
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
        provider_event_id=provider_event_id,
        venue=venue,
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
        raw_hash=hashlib.sha256(provider_event_id.encode()).hexdigest(),
    )


async def _counts(pool, *, order_id: UUID, execution_id: int) -> dict[str, object]:
    async with pool.acquire() as conn:
        order = await conn.fetchrow(
            "SELECT status, filled_quantity FROM orders WHERE order_id = $1", order_id
        )
        fills_count = await conn.fetchval(
            "SELECT count(*) FROM fills WHERE order_id = $1", order_id
        )
        positions_count = await conn.fetchval(
            "SELECT count(*) FROM positions WHERE execution_id = $1", execution_id
        )
    return {
        "status": order["status"],
        "filled_quantity": order["filled_quantity"],
        "fills_count": fills_count,
        "positions_count": positions_count,
    }


async def test_venue_spoofed_fill_event_is_rejected_no_state_change(pool):
    """(1) 공격자가 실제로는 bitget 주문인데 이벤트의 venue를 다른 거래소로
    자칭한다 — 위조 이벤트가 진짜 주문을 체결시키지 못해야 한다."""
    user_id = await create_test_tenant(pool)
    execution_id = await _seed_execution(pool, user_id)
    quantity = Decimal("5")
    exchange_order_id = f"ex-tamper-venue-{uuid4().hex}"
    order_id, client_order_id = await _insert_order(
        pool, user_id, execution_id=execution_id, quantity=quantity,
        exchange_order_id=exchange_order_id,
    )
    tampered = _fill_event(
        venue="binance",  # 실제 주문의 exchange='bitget'과 불일치(위조)
        client_order_id=client_order_id,
        exchange_order_id=exchange_order_id,
        quantity=quantity,
        provider_event_id=f"tamper-venue-{uuid4().hex}",
    )

    await InboxProcessor(pool).ingest(tampered)

    result = await _counts(pool, order_id=order_id, execution_id=execution_id)
    assert result["status"] == "SUBMITTED"  # 원래 상태 그대로 — FILLED로 전이 안 됨
    assert result["filled_quantity"] == Decimal("0")
    assert result["fills_count"] == 0
    assert result["positions_count"] == 0

    async with pool.acquire() as conn:
        state = await conn.fetchval(
            "SELECT state FROM provider_event_inbox WHERE venue = $1 AND provider_event_id = $2",
            "binance",
            tampered.provider_event_id,
        )
    assert state == "IGNORED"


async def test_forged_order_reference_is_rejected_no_orphan_state(pool):
    """(2) 존재하지 않는 client_order_id/exchange_order_id를 담은 위조 이벤트 —
    아무 주문에도 매칭되지 않아야 하고, 어떤 주문의 fills/positions도
    변하지 않아야 한다."""
    user_id = await create_test_tenant(pool)
    execution_id = await _seed_execution(pool, user_id)
    quantity = Decimal("3")
    exchange_order_id = f"ex-tamper-real-{uuid4().hex}"
    order_id, _client_order_id = await _insert_order(
        pool, user_id, execution_id=execution_id, quantity=quantity,
        exchange_order_id=exchange_order_id,
    )
    forged = _fill_event(
        venue="bitget",
        client_order_id=f"cid-forged-{uuid4().hex}",
        exchange_order_id=f"ex-forged-{uuid4().hex}",
        quantity=Decimal("999"),
        provider_event_id=f"tamper-forged-{uuid4().hex}",
    )

    await InboxProcessor(pool).ingest(forged)

    result = await _counts(pool, order_id=order_id, execution_id=execution_id)
    assert result["status"] == "SUBMITTED"  # 실제 주문은 전혀 건드려지지 않음
    assert result["filled_quantity"] == Decimal("0")
    assert result["fills_count"] == 0
    assert result["positions_count"] == 0

    async with pool.acquire() as conn:
        state = await conn.fetchval(
            "SELECT state FROM provider_event_inbox "
            "WHERE venue = 'bitget' AND provider_event_id = $1",
            forged.provider_event_id,
        )
    assert state == "IGNORED"


async def test_replay_after_terminal_fill_does_not_double_apply(pool):
    """(3) 정상 체결로 이미 FILLED 종결된 주문에, 수량을 부풀린 두 번째(변조)
    체결 이벤트를 재전송해도 fills/positions가 추가로 늘지 않는다 — 이중지급
    시도가 거부됨을 행 수로 확인한다."""
    user_id = await create_test_tenant(pool)
    execution_id = await _seed_execution(pool, user_id)
    quantity = Decimal("5")
    exchange_order_id = f"ex-tamper-replay-{uuid4().hex}"
    order_id, client_order_id = await _insert_order(
        pool, user_id, execution_id=execution_id, quantity=quantity,
        exchange_order_id=exchange_order_id,
    )
    legit = _fill_event(
        venue="bitget",
        client_order_id=client_order_id,
        exchange_order_id=exchange_order_id,
        quantity=quantity,
        provider_event_id=f"tamper-replay-legit-{uuid4().hex}",
    )
    processor = InboxProcessor(pool)
    await processor.ingest(legit)

    before = await _counts(pool, order_id=order_id, execution_id=execution_id)
    assert before["status"] == "FILLED"
    assert before["fills_count"] == 1
    assert before["positions_count"] == 1

    tampered_replay = _fill_event(
        venue="bitget",
        client_order_id=client_order_id,
        exchange_order_id=exchange_order_id,
        quantity=quantity * 10,  # 부풀린(위조) 수량으로 추가 지급을 노림
        # 새 이벤트 id — 중복흡수(F9)와 다른 경로
        provider_event_id=f"tamper-replay-forged-{uuid4().hex}",
    )
    await processor.ingest(tampered_replay)

    after = await _counts(pool, order_id=order_id, execution_id=execution_id)
    assert after["status"] == "FILLED"  # 그대로 — 재전이 없음
    assert after["filled_quantity"] == quantity  # 부풀린 수량이 반영되지 않음
    assert after["fills_count"] == 1  # 새 fills 행이 추가되지 않음
    assert after["positions_count"] == 1  # 포지션 행 수 불변(0행 변경)

    async with pool.acquire() as conn:
        position_qty = await conn.fetchval(
            "SELECT quantity FROM positions WHERE execution_id = $1", execution_id
        )
        state = await conn.fetchval(
            "SELECT state FROM provider_event_inbox "
            "WHERE venue = 'bitget' AND provider_event_id = $1",
            tampered_replay.provider_event_id,
        )
    assert position_qty == quantity  # 포지션 수량 자체도 부풀려지지 않음
    assert state == "PROCESSED"  # 종결 후 재전달 — 무시가 아니라 "처리 완료"로 마킹(§4.2)
