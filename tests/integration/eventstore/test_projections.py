"""FA-14 통합테스트 — 실DB(TEST_DATABASE_URL)에서 `order_events` 전건을 재생한
`orders` 투영이 현재 `orders` 테이블과 필드 단위로 같은지 검증한다.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#§9 FA-14 DoD.
DoD(1) 필드 단위 동일(불일치 1건이면 FAIL). DoD(2) 배선증명 negative — 원천
이벤트 1건을 고의로 빼면 대조가 실제로 FAIL함을 같은 테스트에서 단언한다
(항상 통과하는 대조는 반려).

`orders` 투영은 2원천이다(task-2050 decision (b)) — `status`/`version`/
`updated_at`은 `order_events`에서, `filled_quantity`/`average_fill_price`/
`fee_total`/`fee_currency`는 `fills`(append-only, L4-08)에서 접는다.
`order_events`가 `payload_hash`만 갖고 실제 payload가 없어 후자 필드들을
이벤트만으로 재구성할 수 없다는 것이 단일 원천 시도에서 나온 발견이다 —
자세한 근거는 `src/core/eventstore/projections/orders.py` 모듈 docstring.

`pos_journal`·`ledger_journal_entry` 재생 검증은
`test_projections_positions.py`·`test_projections_ledger.py`로 분리돼 있다
(책임 단위 분할, CLAUDE.md ADR-2026-09-10-C §7).
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from src.core.eventstore.projections import orders as orders_projection
from src.data.models.trading import OrderSide, OrderStatus
from src.services.oms.adapters.fills_repository import FillsRepository
from src.services.oms.adapters.order_events_repository import PostgresOrderEventRepository
from src.services.oms.adapters.order_repository import PostgresOrderRepository
from src.services.oms.application.inbox_processor import InboxProcessor
from src.services.oms.contracts.v1_events import FillEvent, OrderTransitionEvent, ProviderOrderEvent
from tests.integration.conftest import create_test_user
from tests.integration.oms.conftest import insert_order


def _clock() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------- orders ---


def _order_event(
    order_id, *, from_status: OrderStatus, to_status: OrderStatus, event: str
) -> OrderTransitionEvent:
    return OrderTransitionEvent(
        order_id=order_id,
        from_status=from_status,
        to_status=to_status,
        event=event,
        reason_code=None,
        actor_subject_id="system",
        trace_id=uuid4(),
        command_id=None,
        provider_event_id=None,
        occurred_at=_clock(),
        payload_hash="e" * 64,
    )


async def _run_three_transitions(pool) -> tuple:
    repo = PostgresOrderRepository()
    user_id = await create_test_user(pool)
    async with pool.acquire() as conn:
        order_id = await insert_order(conn, user_id, status="CREATED")
        await repo.transition(
            conn,
            order_id=order_id,
            expected_status=OrderStatus.CREATED,
            expected_version=0,
            new_status=OrderStatus.VALIDATED,
            patch={},
            event=_order_event(
                order_id,
                from_status=OrderStatus.CREATED,
                to_status=OrderStatus.VALIDATED,
                event="VALIDATED",
            ),
        )
        await repo.transition(
            conn,
            order_id=order_id,
            expected_status=OrderStatus.VALIDATED,
            expected_version=1,
            new_status=OrderStatus.SUBMITTED,
            patch={},
            event=_order_event(
                order_id,
                from_status=OrderStatus.VALIDATED,
                to_status=OrderStatus.SUBMITTED,
                event="SUBMITTED",
            ),
        )
        await repo.transition(
            conn,
            order_id=order_id,
            expected_status=OrderStatus.SUBMITTED,
            expected_version=2,
            new_status=OrderStatus.ACKNOWLEDGED,
            patch={},
            event=_order_event(
                order_id,
                from_status=OrderStatus.SUBMITTED,
                to_status=OrderStatus.ACKNOWLEDGED,
                event="ACKNOWLEDGED",
            ),
        )
        events = await PostgresOrderEventRepository().timeline(conn, order_id)
        row = await conn.fetchrow(
            "SELECT status, version FROM orders WHERE order_id = $1", order_id
        )
    return order_id, events, row


async def test_orders_projection_matches_current_status_after_full_replay(pool):
    order_id, events, row = await _run_three_transitions(pool)

    projected = orders_projection.project(order_id, events)

    assert projected.status.value == row["status"]
    assert projected.version == row["version"]


async def test_orders_projection_detects_dropped_event(pool):
    """DoD(2) 배선증명: 원천 이벤트 1건(첫 전이)을 빼면 재생이 그 사실을
    `EventChainBrokenError`로 즉시 드러낸다(항상 통과하는 대조가 아님)."""
    order_id, events, row = await _run_three_transitions(pool)
    assert len(events) == 3
    assert row["status"] == "ACKNOWLEDGED"

    with pytest.raises(orders_projection.EventChainBrokenError):
        orders_projection.project(order_id, events[1:])  # 첫 이벤트 누락


def _provider_fill_event(
    *, client_order_id: str, quantity: Decimal, price: Decimal
) -> ProviderOrderEvent:
    """A provider fill event resolvable to `client_order_id` via
    `InboxProcessor._resolve_order_id` (`exchange_order_id` is a fresh
    unrelated string — the OR-clause still matches on `client_order_id`)."""
    fill_id = f"fill-{uuid4().hex}"
    now = datetime.now(timezone.utc)
    fill = FillEvent(
        provider_fill_id=fill_id,
        venue="bitget",
        order_id=None,
        exchange_order_id=f"ex-{uuid4().hex}",
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        quantity=quantity,
        price=price,
        fee=Decimal("1"),
        fee_currency="USDT",
        liquidity="TAKER",
        venue_ts=now,
    )
    return ProviderOrderEvent(
        provider_event_id=fill_id,
        venue="bitget",
        venue_symbol="BTCUSDT",
        exchange_order_id=fill.exchange_order_id,
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


async def _run_two_fills_via_inbox(pool) -> tuple:
    """Drive CREATED->VALIDATED->SUBMITTED via `order_repository.transition()`
    (same as `_run_three_transitions`), then two partial fills through
    `InboxProcessor.ingest()` — the real L4-15 production path — so the
    resulting order_events/fills/orders triple exercises the two-source
    projection exactly like production, including the version double-bump
    documented in `src/core/eventstore/projections/orders.py`."""
    repo = PostgresOrderRepository()
    user_id = await create_test_user(pool)
    async with pool.acquire() as conn:
        order_id = await insert_order(conn, user_id, status="CREATED", quantity=Decimal("10"))
        client_order_id = await conn.fetchval(
            "SELECT client_order_id FROM orders WHERE order_id = $1", order_id
        )
        await repo.transition(
            conn,
            order_id=order_id,
            expected_status=OrderStatus.CREATED,
            expected_version=0,
            new_status=OrderStatus.VALIDATED,
            patch={},
            event=_order_event(
                order_id,
                from_status=OrderStatus.CREATED,
                to_status=OrderStatus.VALIDATED,
                event="VALIDATED",
            ),
        )
        await repo.transition(
            conn,
            order_id=order_id,
            expected_status=OrderStatus.VALIDATED,
            expected_version=1,
            new_status=OrderStatus.SUBMITTED,
            patch={},
            event=_order_event(
                order_id,
                from_status=OrderStatus.VALIDATED,
                to_status=OrderStatus.SUBMITTED,
                event="SUBMITTED",
            ),
        )

    processor = InboxProcessor(pool)
    first = _provider_fill_event(
        client_order_id=client_order_id, quantity=Decimal("4"), price=Decimal("100")
    )
    second = _provider_fill_event(
        client_order_id=client_order_id, quantity=Decimal("6"), price=Decimal("120")
    )
    assert await processor.ingest(first) is True
    assert await processor.ingest(second) is True

    async with pool.acquire() as conn:
        events = await PostgresOrderEventRepository().timeline(conn, order_id)
        fills = await FillsRepository().list_for_order(conn, order_id)
        row = await conn.fetchrow(
            "SELECT status, version, filled_quantity, average_fill_price, fee_total, "
            "fee_currency FROM orders WHERE order_id = $1",
            order_id,
        )
    return order_id, events, fills, row


async def test_orders_projection_matches_current_row_after_full_replay_with_fills(pool):
    """DoD(1) — full field parity, including the two-source fields fills
    contribute and the version count a FILL event's silent double-bump would
    otherwise drift by (see orders.py module docstring)."""
    order_id, events, fills, row = await _run_two_fills_via_inbox(pool)

    projected = orders_projection.project(order_id, events, fills)

    assert projected.status.value == row["status"]
    assert projected.version == row["version"]
    assert projected.filled_quantity == row["filled_quantity"]
    assert projected.average_fill_price == row["average_fill_price"]
    assert projected.fee_total == row["fee_total"]
    assert projected.fee_currency == row["fee_currency"]


async def test_orders_projection_detects_dropped_fill(pool):
    """DoD(2) negative (ii) — dropping one `fills` row (the second partial
    fill) makes the replayed `filled_quantity` actually diverge from the
    current `orders` row."""
    order_id, events, fills, row = await _run_two_fills_via_inbox(pool)
    assert len(fills) == 2

    dropped = orders_projection.project(order_id, events, fills[:1])

    assert dropped.filled_quantity != row["filled_quantity"]
