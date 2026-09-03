"""LC-15a `application/payouts.py`/`adapters/postgres_payout_repository.py`
통합테스트 — 실 DB(TEST_DATABASE_URL) 대상.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§4.4, §8.2
test_payouts.py, §9 LC-15.
DoD: 홀드 창 경과 후 RELEASE, PAID 후 `PLATFORM:PAYOUT_CLEARING` 증가, 같은
(seller_user_id, period_end) 배치 재실행 멱등 — 세 케이스 전부 실 DB로 단언.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from src.core.db.conditional_write import ConcurrencyConflictError
from src.data.models.base import Currency
from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.ledger.adapters.postgres_balance_repository import PostgresBalanceRepository
from src.foundation.ledger.adapters.postgres_hold_repository import PostgresHoldRepository
from src.foundation.ledger.adapters.postgres_journal_repository import PostgresJournalRepository
from src.foundation.ledger.adapters.postgres_payout_repository import PostgresPayoutRepository
from src.foundation.ledger.application.payouts import (
    UnknownPayoutBatchError,
    mark_payout_paid,
    schedule_payouts,
)
from src.foundation.ledger.application.purchase_flow import CaptureResult, capture_hold, place_hold
from src.foundation.ledger.contracts.v1 import UserSub
from src.foundation.ledger.domain.chart_of_accounts import PLATFORM_PAYOUT_CLEARING
from src.foundation.ledger.domain.chart_of_accounts import user_account as ua
from src.foundation.ledger.domain.payout_schedule import CaptureRecord
from tests.integration.conftest import create_test_user

_TEST_PURPOSE = "TEST_PAYOUT_SCHEDULE"
_WINDOW = timedelta(days=7)


def _clock() -> datetime:
    return datetime.now(timezone.utc)


class _RealPorts:
    def __init__(self, pool) -> None:
        self.journal = PostgresJournalRepository(pool)
        self.balances = PostgresBalanceRepository(pool)
        self.audit = PostgresAuditEventRepository(pool)
        self.holds = PostgresHoldRepository(pool)
        self.payouts = PostgresPayoutRepository(pool)
        self.clock = _clock


@pytest.fixture
def ports(pool):
    return _RealPorts(pool)


async def _seed_available(pool, user_id, amount: Decimal) -> None:
    code = ua(user_id, UserSub.AVAILABLE)
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO ledger_account (account_code, account_type, currency, allow_negative) "
            "VALUES ($1, 'LIABILITY', 'KRW', FALSE) ON CONFLICT (account_code) DO NOTHING",
            code,
        )
        await conn.execute(
            "INSERT INTO ledger_balance (account_id, balance, allow_negative, last_entry_seq) "
            "SELECT account_id, $2, FALSE, 0 FROM ledger_account WHERE account_code = $1 "
            "ON CONFLICT (account_id) DO UPDATE SET balance = $2",
            code,
            amount,
        )
        await conn.execute(
            "INSERT INTO user_wallets (user_id, balance) VALUES ($1, $2) "
            "ON CONFLICT (user_id) DO UPDATE SET balance = $2",
            user_id,
            amount,
        )


async def _balance(pool, account_code: str) -> Decimal:
    async with pool.acquire() as conn:
        value = await conn.fetchval(
            "SELECT lb.balance FROM ledger_balance lb JOIN ledger_account la "
            "ON la.account_id = lb.account_id WHERE la.account_code = $1",
            account_code,
        )
    return value if value is not None else Decimal("0")


async def _captured_hold(pool, ports, buyer, seller, price: Decimal) -> CaptureResult:
    reference = f"test-payout:{uuid4()}"
    await _seed_available(pool, buyer, price)
    async with pool.acquire() as conn, conn.transaction():
        hold = await place_hold(
            conn, buyer_id=buyer, amount=price, purpose=_TEST_PURPOSE, reference=reference,
            expires_at=_clock() + timedelta(minutes=15), actor_subject_id=buyer, trace_id=uuid4(),
            journal=ports.journal, balances=ports.balances, audit=ports.audit, clock=ports.clock,
            holds=ports.holds,
        )
        capture = await capture_hold(
            conn, hold, seller_id=seller, commission_rate=Decimal("0.15"),
            actor_subject_id=buyer, trace_id=uuid4(), now=_clock(),
            journal=ports.journal, balances=ports.balances, audit=ports.audit, clock=ports.clock,
            holds=ports.holds,
        )
    return capture


def _record_for(capture: CaptureResult, seller) -> CaptureRecord:
    return CaptureRecord(
        entry_id=capture.entry.entry_id,
        seller_user_id=seller,
        amount=capture.payout_amount,
        currency=Currency.KRW,
        captured_at=capture.entry.posted_at,
    )


async def test_schedule_payouts_releases_only_after_window_elapsed(pool, ports):
    buyer = await create_test_user(pool)
    seller = await create_test_user(pool)
    capture = await _captured_hold(pool, ports, buyer, seller, Decimal("100.00"))
    seller_pending = ua(seller, UserSub.PENDING_PAYOUT)
    seller_available = ua(seller, UserSub.AVAILABLE)
    assert await _balance(pool, seller_pending) == Decimal("85.00")

    posted_at = capture.entry.posted_at
    period_start = posted_at.date()
    period_end = period_start + timedelta(days=1)
    record = _record_for(capture, seller)

    # 창 미경과: 아직 정산 대상이 아니다(negative case) — 잔액도 그대로.
    async with pool.acquire() as conn, conn.transaction():
        too_early = await schedule_payouts(
            conn, [record], period_start=period_start, period_end=period_end,
            now=posted_at, actor_subject_id=None, settlement_window=_WINDOW,
            journal=ports.journal, balances=ports.balances, audit=ports.audit,
            clock=ports.clock, payouts=ports.payouts,
        )
    assert too_early == []
    assert await _balance(pool, seller_pending) == Decimal("85.00")

    async with pool.acquire() as conn, conn.transaction():
        batches = await schedule_payouts(
            conn, [record], period_start=period_start, period_end=period_end,
            now=posted_at + _WINDOW + timedelta(seconds=1), actor_subject_id=None,
            settlement_window=_WINDOW,
            journal=ports.journal, balances=ports.balances, audit=ports.audit,
            clock=ports.clock, payouts=ports.payouts,
        )

    assert len(batches) == 1
    batch = batches[0]
    assert batch.state == "RELEASED"
    assert batch.seller_user_id == seller
    assert batch.amount == Decimal("85.00")
    assert batch.capture_entry_ids == [capture.entry.entry_id]
    assert batch.release_entry_id is not None
    assert await _balance(pool, seller_pending) == Decimal("0.00")
    assert await _balance(pool, seller_available) == Decimal("85.00")


async def test_schedule_payouts_same_batch_key_is_idempotent(pool, ports):
    buyer = await create_test_user(pool)
    seller = await create_test_user(pool)
    capture = await _captured_hold(pool, ports, buyer, seller, Decimal("50.00"))
    seller_pending = ua(seller, UserSub.PENDING_PAYOUT)
    seller_available = ua(seller, UserSub.AVAILABLE)

    posted_at = capture.entry.posted_at
    period_start = posted_at.date()
    period_end = period_start + timedelta(days=1)
    now = posted_at + _WINDOW + timedelta(seconds=1)
    record = _record_for(capture, seller)

    async def _run() -> list:
        async with pool.acquire() as conn, conn.transaction():
            return await schedule_payouts(
                conn, [record], period_start=period_start, period_end=period_end,
                now=now, actor_subject_id=None, settlement_window=_WINDOW,
                journal=ports.journal, balances=ports.balances, audit=ports.audit,
                clock=ports.clock, payouts=ports.payouts,
            )

    first = await _run()
    second = await _run()

    assert len(first) == 1
    assert len(second) == 1
    assert first[0].batch_id == second[0].batch_id
    assert first[0].release_entry_id == second[0].release_entry_id

    async with pool.acquire() as conn:
        entry_count = await conn.fetchval(
            "SELECT COUNT(*) FROM ledger_journal_entry WHERE event_ref = $1",
            f"payout_batch:{seller}:{period_end.isoformat()}",
        )
        batch_count = await conn.fetchval(
            "SELECT COUNT(*) FROM ledger_payout_batch "
            "WHERE seller_user_id = $1 AND period_end = $2",
            seller,
            period_end,
        )
        item_count = await conn.fetchval(
            "SELECT COUNT(*) FROM ledger_payout_item WHERE batch_id = $1", first[0].batch_id
        )
    assert entry_count == 1
    assert batch_count == 1
    assert item_count == 1
    assert await _balance(pool, seller_pending) == Decimal("0.00")
    assert await _balance(pool, seller_available) == Decimal("42.50")


async def test_mark_payout_paid_moves_available_to_payout_clearing(pool, ports):
    buyer = await create_test_user(pool)
    seller = await create_test_user(pool)
    capture = await _captured_hold(pool, ports, buyer, seller, Decimal("100.00"))
    posted_at = capture.entry.posted_at
    period_start = posted_at.date()
    period_end = period_start + timedelta(days=1)
    record = _record_for(capture, seller)

    async with pool.acquire() as conn, conn.transaction():
        batches = await schedule_payouts(
            conn, [record], period_start=period_start, period_end=period_end,
            now=posted_at + _WINDOW + timedelta(seconds=1), actor_subject_id=None,
            settlement_window=_WINDOW,
            journal=ports.journal, balances=ports.balances, audit=ports.audit,
            clock=ports.clock, payouts=ports.payouts,
        )
    batch = batches[0]
    seller_available = ua(seller, UserSub.AVAILABLE)
    clearing_before = await _balance(pool, PLATFORM_PAYOUT_CLEARING)
    assert await _balance(pool, seller_available) == Decimal("85.00")

    admin = await create_test_user(pool)
    async with pool.acquire() as conn, conn.transaction():
        paid = await mark_payout_paid(
            conn, batch.batch_id, admin_id=admin, external_ref="test-wire-001",
            journal=ports.journal, balances=ports.balances, audit=ports.audit,
            clock=ports.clock, payouts=ports.payouts,
        )

    assert paid.state == "PAID"
    assert paid.paid_entry_id is not None
    assert await _balance(pool, seller_available) == Decimal("0.00")
    assert await _balance(pool, PLATFORM_PAYOUT_CLEARING) == clearing_before + Decimal("85.00")

    # 이미 PAID인 배치를 다시 확정하려는 시도는 거부(negative case).
    async with pool.acquire() as conn, conn.transaction():
        with pytest.raises(ConcurrencyConflictError):
            await mark_payout_paid(
                conn, batch.batch_id, admin_id=admin, external_ref="test-wire-002",
                journal=ports.journal, balances=ports.balances, audit=ports.audit,
                clock=ports.clock, payouts=ports.payouts,
            )


async def test_mark_payout_paid_unknown_batch_is_rejected(pool, ports):
    admin = await create_test_user(pool)
    async with pool.acquire() as conn, conn.transaction():
        with pytest.raises(UnknownPayoutBatchError):
            await mark_payout_paid(
                conn, uuid4(), admin_id=admin, external_ref="test-wire-unknown",
                journal=ports.journal, balances=ports.balances, audit=ports.audit,
                clock=ports.clock, payouts=ports.payouts,
            )
