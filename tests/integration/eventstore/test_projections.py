"""FA-14 통합테스트 — 실DB(TEST_DATABASE_URL)에서 `order_events`·`pos_journal`·
`ledger_journal_entry`+`ledger_posting_line` 전건을 재생한 투영이 현재 테이블
(`orders`·`pos_snapshot`·`ledger_balance`)과 필드 단위로 같은지 검증한다.

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
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from src.core.eventstore.projections import ledger as ledger_projection
from src.core.eventstore.projections import orders as orders_projection
from src.core.eventstore.projections import positions as positions_projection
from src.data.models.base import AssetClass, Currency, Money
from src.data.models.trading import OrderSide, OrderStatus
from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.ledger.adapters.postgres_balance_repository import PostgresBalanceRepository
from src.foundation.ledger.adapters.postgres_journal_repository import PostgresJournalRepository
from src.foundation.ledger.application.post_entry import post_entry
from src.foundation.ledger.contracts.v1 import AccountType, LedgerEvent, LedgerEventType, UserSub
from src.foundation.ledger.domain.chart_of_accounts import user_account
from src.foundation.positions.adapters.postgres_journal_repository import (
    PostgresJournalRepository as PositionsJournalRepository,
)
from src.foundation.positions.adapters.postgres_snapshot_repository import (
    PostgresSnapshotRepository,
)
from src.foundation.positions.application.record_fill import record_fill
from src.foundation.positions.contracts.v1 import CostMethod, RecordFillCommand
from src.foundation.positions.domain.position_key import PositionKey
from src.services.oms.adapters.fills_repository import FillsRepository
from src.services.oms.adapters.order_events_repository import PostgresOrderEventRepository
from src.services.oms.adapters.order_repository import PostgresOrderRepository
from src.services.oms.application.inbox_processor import InboxProcessor
from src.services.oms.contracts.v1_events import FillEvent, OrderTransitionEvent, ProviderOrderEvent
from tests.integration.conftest import create_test_tenant, create_test_user
from tests.integration.foundation.positions.conftest import create_pos_account, open_position
from tests.integration.oms.conftest import insert_order

_OCCURRED_AT = datetime(2026, 1, 1, tzinfo=timezone.utc)


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
            conn, order_id=order_id, expected_status=OrderStatus.CREATED, expected_version=0,
            new_status=OrderStatus.VALIDATED, patch={},
            event=_order_event(
                order_id, from_status=OrderStatus.CREATED, to_status=OrderStatus.VALIDATED,
                event="VALIDATED",
            ),
        )
        await repo.transition(
            conn, order_id=order_id, expected_status=OrderStatus.VALIDATED, expected_version=1,
            new_status=OrderStatus.SUBMITTED, patch={},
            event=_order_event(
                order_id, from_status=OrderStatus.VALIDATED, to_status=OrderStatus.SUBMITTED,
                event="SUBMITTED",
            ),
        )
        await repo.transition(
            conn, order_id=order_id, expected_status=OrderStatus.SUBMITTED, expected_version=2,
            new_status=OrderStatus.ACKNOWLEDGED, patch={},
            event=_order_event(
                order_id, from_status=OrderStatus.SUBMITTED, to_status=OrderStatus.ACKNOWLEDGED,
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
            conn, order_id=order_id, expected_status=OrderStatus.CREATED, expected_version=0,
            new_status=OrderStatus.VALIDATED, patch={},
            event=_order_event(
                order_id, from_status=OrderStatus.CREATED, to_status=OrderStatus.VALIDATED,
                event="VALIDATED",
            ),
        )
        await repo.transition(
            conn, order_id=order_id, expected_status=OrderStatus.VALIDATED, expected_version=1,
            new_status=OrderStatus.SUBMITTED, patch={},
            event=_order_event(
                order_id, from_status=OrderStatus.VALIDATED, to_status=OrderStatus.SUBMITTED,
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


# ------------------------------------------------------------- positions ---


async def _record_two_fills(pool) -> tuple:
    journal = PositionsJournalRepository(pool)
    snapshots = PostgresSnapshotRepository(pool)
    audit = PostgresAuditEventRepository(pool)
    tenant_id = await create_test_tenant(pool)
    account_id = await create_pos_account(pool, tenant_id)
    position_key = str(
        PositionKey(
            venue="TESTVENUE", instrument_id=f"INST{uuid4().hex[:8]}", strategy_id="default",
            execution_id="paper", portfolio_id=uuid4(),
        )
    )
    await open_position(
        pool, tenant_id=tenant_id, account_id=account_id, position_key=position_key
    )
    order_id = uuid4()

    async def _fill(side: OrderSide, quantity: Decimal, price: Decimal, fill_seq: int):
        async with pool.acquire() as conn, conn.transaction():
            return await record_fill(
                conn,
                RecordFillCommand(
                    tenant_id=tenant_id, account_id=account_id, position_key=position_key,
                    order_id=order_id, fill_seq=fill_seq, side=side, quantity=quantity,
                    price=Money(amount=price, currency=Currency.KRW), fee=None,
                    occurred_at=_OCCURRED_AT, trace_id=uuid4(),
                ),
                asset_class=AssetClass.CRYPTO, journal=journal, snapshots=snapshots,
                audit=audit, clock=_clock,
            )

    await _fill(OrderSide.BUY, Decimal("10"), Decimal("100"), 1)
    await _fill(OrderSide.SELL, Decimal("4"), Decimal("120"), 2)

    async with pool.acquire() as conn:
        entries = await journal.list_for(conn, position_key)
        row = await conn.fetchrow(
            "SELECT quantity, avg_cost, realized_pnl_base, fees_base, funding_base, "
            "last_journal_seq FROM pos_snapshot WHERE position_key = $1",
            position_key,
        )
    return position_key, entries, row


async def test_positions_projection_matches_current_snapshot_after_full_replay(pool):
    position_key, entries, row = await _record_two_fills(pool)

    folded = positions_projection.project(
        entries, position_key=position_key, cost_method=CostMethod.FIFO,
        asset_class=AssetClass.CRYPTO,
    )

    assert folded.quantity == row["quantity"]
    assert folded.avg_cost == row["avg_cost"]
    assert folded.realized_pnl_base == row["realized_pnl_base"]
    assert folded.fees_base == row["fees_base"]
    assert folded.funding_base == row["funding_base"]
    assert folded.last_journal_seq == row["last_journal_seq"]


async def test_positions_projection_detects_dropped_entry(pool):
    """DoD(2) 배선증명: 원천 저널 엔트리 1건(두 번째 체결)을 빼면 재생
    수량이 현재 스냅샷과 실제로 달라진다."""
    position_key, entries, row = await _record_two_fills(pool)
    assert len(entries) == 2

    dropped = positions_projection.project(
        entries[:1], position_key=position_key, cost_method=CostMethod.FIFO,
        asset_class=AssetClass.CRYPTO,
    )

    assert dropped.quantity != row["quantity"]


# ----------------------------------------------------------------ledger ---


async def _create_ledger_test_account(
    pool, user_id, sub: UserSub, *, kind: AccountType, allow_negative: bool = False
) -> str:
    """`USER:{uuid}:{sub}` 계정 하나를 만든다 — `account_type()`이 아는
    형식(USER:*/PLATFORM:*의 고정 이름 4종)만 `posting_rules.lines_for`를
    통과하므로, `PLATFORM:TEST_*` 같은 임의 이름(다른 디렉터리의
    `create_ledger_account`)은 MANUAL_ADJUSTMENT 경로에 쓸 수 없다."""
    code = user_account(user_id, sub)
    async with pool.acquire() as conn:
        account_id = await conn.fetchval(
            "INSERT INTO ledger_account (account_code, account_type, currency, allow_negative) "
            "VALUES ($1, $2, $3, $4) RETURNING account_id",
            code, kind.value, Currency.KRW.value, allow_negative,
        )
        await conn.execute(
            "INSERT INTO ledger_balance (account_id, allow_negative, last_entry_seq) "
            "VALUES ($1, $2, 0)",
            account_id, allow_negative,
        )
    return code


async def _post_two_manual_adjustments(pool) -> tuple:
    # MANUAL_ADJUSTMENT lets a test post between two freshly-created accounts
    # directly, isolated from LC-6's shared PLATFORM:* seed accounts. Debit
    # goes to a RECEIVABLE(ASSET, debit-normal) test account and credit to an
    # AVAILABLE(LIABILITY, credit-normal) one so both sides increase — no
    # negative-balance rejection to work around.
    journal = PostgresJournalRepository(pool)
    balances = PostgresBalanceRepository(pool)
    audit = PostgresAuditEventRepository(pool)

    debit_code = await _create_ledger_test_account(
        pool, uuid4(), UserSub.RECEIVABLE, kind=AccountType.ASSET, allow_negative=True
    )
    credit_code = await _create_ledger_test_account(
        pool, uuid4(), UserSub.AVAILABLE, kind=AccountType.LIABILITY
    )

    async with pool.acquire() as conn:
        last = await journal.last(conn)
    start_seq = 0 if last is None else last.sequence_no

    async def _adjust(amount: Decimal):
        event = LedgerEvent(
            event_type=LedgerEventType.MANUAL_ADJUSTMENT,
            event_ref=f"adj:{uuid4().hex}",
            tenant_id=None,
            actor_subject_id=None,
            trace_id=uuid4(),
            amount=amount,
            currency=Currency.KRW,
            parties={},
            extra={"debit_account": debit_code, "credit_account": credit_code},
        )
        async with pool.acquire() as conn, conn.transaction():
            return await post_entry(
                conn, event, journal=journal, balances=balances, audit=audit, clock=_clock,
            )

    await _adjust(Decimal("10.00"))
    await _adjust(Decimal("5.00"))

    async with pool.acquire() as conn:
        entries = await journal.list_since(conn, start_seq)
        row = await conn.fetchrow(
            "SELECT lb.balance, lb.last_entry_seq FROM ledger_balance lb "
            "JOIN ledger_account la ON la.account_id = lb.account_id WHERE la.account_code = $1",
            debit_code,
        )
    return debit_code, entries, row


async def test_ledger_projection_matches_current_balance_after_full_replay(pool):
    debit_code, entries, row = await _post_two_manual_adjustments(pool)

    projected = ledger_projection.project(entries)

    assert projected[debit_code].balance == row["balance"]
    assert projected[debit_code].last_entry_seq == row["last_entry_seq"]


async def test_ledger_projection_detects_dropped_entry(pool):
    """DoD(2) 배선증명: 원천 분개 1건(두 번째 조정)을 빼면 재생 잔액이 현재
    `ledger_balance`와 실제로 달라진다."""
    debit_code, entries, row = await _post_two_manual_adjustments(pool)
    assert len(entries) == 2

    dropped = ledger_projection.project(entries[:1])

    assert dropped[debit_code].balance != row["balance"]
