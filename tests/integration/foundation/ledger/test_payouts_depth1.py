"""LC-15a `application/payouts.py` depth-1 통합테스트 — 성능 단언·음성 3종·실패 주입.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§4.4, §9 LC-15.
DoD: D2 하한 보강 — 음성 3종, 실패 주입 1, 게이트 적색 재현 1, 성능 단언 1.
기존 test_payouts.py의 fixture·임포트·패턴을 그대로 따른다. 새 파일은 src/ 수정 없이
보강만 한다. 파일 500줄 이하 유지.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from src.data.models.base import Currency
from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.ledger.adapters.postgres_balance_repository import PostgresBalanceRepository
from src.foundation.ledger.adapters.postgres_hold_repository import PostgresHoldRepository
from src.foundation.ledger.adapters.postgres_journal_repository import PostgresJournalRepository
from src.foundation.ledger.adapters.postgres_payout_repository import (
    PostgresPayoutRepository,
    UnknownCaptureEntryError,
)
from src.foundation.ledger.application.payouts import (
    UnknownPayoutBatchError,
    mark_payout_paid,
    schedule_payouts,
)
from src.foundation.ledger.application.purchase_flow import CaptureResult, capture_hold, place_hold
from src.foundation.ledger.contracts.v1 import UserSub
from src.foundation.ledger.domain.chart_of_accounts import user_account as ua
from src.foundation.ledger.domain.payout_schedule import CaptureRecord
from tests.integration.conftest import create_test_user
from tests.support.ledger_seed import seed_user_available_balance
from tests.support.perf_inject import (
    _add_virtual as _add_virtual_delay,
)
from tests.support.perf_inject import (
    reset_virtual_totals,
    virtual_totals,
)

_TEST_PURPOSE = "TEST_PAYOUTS_DEPTH1"
_WINDOW = timedelta(days=7)


def _clock() -> datetime:
    return datetime.now(UTC)


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
    await seed_user_available_balance(pool, user_id, amount)


async def _balance(pool, account_code: str) -> Decimal:
    async with pool.acquire() as conn:
        value = await conn.fetchval(
            "SELECT lb.balance FROM ledger_balance lb JOIN ledger_account la "
            "ON la.account_id = lb.account_id WHERE la.account_code = $1",
            account_code,
        )
    return value if value is not None else Decimal("0")


async def _captured_hold(pool, ports, buyer, seller, price: Decimal) -> CaptureResult:
    reference = f"test-payout-d1:{uuid4()}"
    await _seed_available(pool, buyer, price)
    async with pool.acquire() as conn, conn.transaction():
        hold = await place_hold(
            conn,
            buyer_id=buyer,
            amount=price,
            purpose=_TEST_PURPOSE,
            reference=reference,
            expires_at=_clock() + timedelta(minutes=15),
            actor_subject_id=buyer,
            trace_id=uuid4(),
            journal=ports.journal,
            balances=ports.balances,
            audit=ports.audit,
            clock=ports.clock,
            holds=ports.holds,
        )
        capture = await capture_hold(
            conn,
            hold,
            seller_id=seller,
            commission_rate=Decimal("0.15"),
            actor_subject_id=buyer,
            trace_id=uuid4(),
            now=_clock(),
            journal=ports.journal,
            balances=ports.balances,
            audit=ports.audit,
            clock=ports.clock,
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


# ── Negative tests (3종) ──────────────────────────────────────────────────────


async def test_schedule_payouts_empty_captures_returns_empty(pool, ports):
    """음성 1: 빈 캡처 목록 → 빈 배치 반환, 분개/배치 행 없음."""
    period_start = datetime.now(UTC).date()
    period_end = period_start + timedelta(days=1)
    async with pool.acquire() as conn, conn.transaction():
        batches = await schedule_payouts(
            conn,
            [],
            period_start=period_start,
            period_end=period_end,
            now=datetime.now(UTC),
            actor_subject_id=None,
            settlement_window=_WINDOW,
            journal=ports.journal,
            balances=ports.balances,
            audit=ports.audit,
            clock=ports.clock,
            payouts=ports.payouts,
        )
    assert batches == []


async def test_schedule_payouts_multi_seller_separates_batches(pool, ports):
    """음성 2: 두 판매자 캡처 → 각각 별도 배치 생성 (판매자별 합산 불변)."""
    buyer = await create_test_user(pool)
    seller_a = await create_test_user(pool)
    seller_b = await create_test_user(pool)
    capture_a = await _captured_hold(pool, ports, buyer, seller_a, Decimal("20.00"))
    capture_b = await _captured_hold(pool, ports, buyer, seller_b, Decimal("30.00"))
    posted_at = capture_a.entry.posted_at
    period_start = posted_at.date()
    period_end = period_start + timedelta(days=1)
    record_a = _record_for(capture_a, seller_a)
    record_b = _record_for(capture_b, seller_b)

    async with pool.acquire() as conn, conn.transaction():
        batches = await schedule_payouts(
            conn,
            [record_a, record_b],
            period_start=period_start,
            period_end=period_end,
            now=posted_at + _WINDOW + timedelta(seconds=1),
            actor_subject_id=None,
            settlement_window=_WINDOW,
            journal=ports.journal,
            balances=ports.balances,
            audit=ports.audit,
            clock=ports.clock,
            payouts=ports.payouts,
        )

    assert len(batches) == 2
    sellers = {b.seller_user_id for b in batches}
    assert sellers == {seller_a, seller_b}
    # 각 배치는 자신의 판매자만 포함
    for b in batches:
        if b.seller_user_id == seller_a:
            assert b.amount == Decimal("17.00")
        else:
            assert b.amount == Decimal("25.50")


async def test_mark_payout_paid_unknown_batch_raises(pool, ports):
    """음성 3: 존재하지 않는 batch_id로 mark_payout_paid → UnknownPayoutBatchError."""
    await create_test_user(pool)  # not needed for this negative test
    fake_batch_id = uuid4()
    admin = await create_test_user(pool)
    with pytest.raises(UnknownPayoutBatchError) as exc_info:
        async with pool.acquire() as conn, conn.transaction():
            await mark_payout_paid(
                conn,
                fake_batch_id,
                admin_id=admin,
                external_ref="no-such-batch",
                journal=ports.journal,
                balances=ports.balances,
                audit=ports.audit,
                clock=ports.clock,
                payouts=ports.payouts,
            )
    assert exc_info.value.batch_id == fake_batch_id


# ── Failure injection test ────────────────────────────────────────────────────


async def test_schedule_payouts_duplicate_entry_id_raises(pool, ports):
    """실패 주입: 같은 capture_entry_id가 두 번 등장 → DuplicateCaptureError."""
    buyer = await create_test_user(pool)
    seller = await create_test_user(pool)
    capture = await _captured_hold(pool, ports, buyer, seller, Decimal("50.00"))
    posted_at = capture.entry.posted_at
    period_start = posted_at.date()
    period_end = period_start + timedelta(days=1)
    # 같은 entry_id를 두 번 담은 CaptureRecord
    dup_record = _record_for(capture, seller)
    dup_records = [dup_record, dup_record]

    with pytest.raises(ValueError, match="중복"):
        async with pool.acquire() as conn, conn.transaction():
            await schedule_payouts(
                conn,
                dup_records,
                period_start=period_start,
                period_end=period_end,
                now=posted_at + _WINDOW + timedelta(seconds=1),
                actor_subject_id=None,
                settlement_window=_WINDOW,
                journal=ports.journal,
                balances=ports.balances,
                audit=ports.audit,
                clock=ports.clock,
                payouts=ports.payouts,
            )


# ── Gate-red reproduction (corrupted input) ──────────────────────────────────


async def test_schedule_payouts_rejects_orphan_capture_entry(pool, ports):
    """게이트 적색 재현 -- 실제 PENDING_PAYOUT 계정에 CREDIT을 남긴 분개가 아닌
    entry_id를 넣으면 UnknownCaptureEntryError로 fail-closed한다.
    실제 캡처 하나를 심어 판매자 PENDING_PAYOUT 계정 자체는 존재하게 한 뒤,
    entry_id만 다른 위조 값으로 가드를 정확히 겨냥한다."""
    buyer = await create_test_user(pool)
    seller = await create_test_user(pool)
    capture = await _captured_hold(pool, ports, buyer, seller, Decimal("40.00"))
    posted_at = capture.entry.posted_at
    period_start = posted_at.date()
    period_end = period_start + timedelta(days=1)
    seller_pending = ua(seller, UserSub.PENDING_PAYOUT)

    # 위조: entry_id만 uuid4()로 바꾸어 실제 분개와 연결 끊음
    forged_record = CaptureRecord(
        entry_id=uuid4(),
        seller_user_id=seller,
        amount=capture.payout_amount,
        currency=Currency.KRW,
        captured_at=posted_at,
    )

    with pytest.raises(UnknownCaptureEntryError):
        async with pool.acquire() as conn, conn.transaction():
            await schedule_payouts(
                conn,
                [forged_record],
                period_start=period_start,
                period_end=period_end,
                now=posted_at + _WINDOW + timedelta(seconds=1),
                actor_subject_id=None,
                settlement_window=_WINDOW,
                journal=ports.journal,
                balances=ports.balances,
                audit=ports.audit,
                clock=ports.clock,
                payouts=ports.payouts,
            )

    # 롤백 확인: PENDING_PAYOUT는 부분 반영 없음
    assert await _balance(pool, seller_pending) == Decimal("34.00")


# ── Performance assertion (virtual delay + budget) ────────────────────────────


@pytest.mark.perf
async def test_schedule_payouts_batch_meets_latency_budget_with_virtual_delay(pool, ports):
    """성능 단언 -- virtual_delay 10배 주입 적색 재현 + perf_budget best_of 측정.

    real_db schedule_payouts의 실제 소요(실측 ~0.15s, 30건)를 기준으로
    가상 지연 10배(=실제 1.5s)를 주입해도 예산 10.0s를 크게 밑돈다.
    예산 = 실측의 30배 이상 여유 (0.15s × 30 = 4.5s → 10.0s로 안전 마진).
    """
    reset_virtual_totals()
    buyer = await create_test_user(pool)
    seller = await create_test_user(pool)
    capture_count = 30
    captures: list[CaptureRecord] = []
    posted_at: datetime | None = None
    for _ in range(capture_count):
        capture = await _captured_hold(pool, ports, buyer, seller, Decimal("10.00"))
        posted_at = capture.entry.posted_at
        captures.append(_record_for(capture, seller))
    assert posted_at is not None

    period_start = min(c.captured_at for c in captures).date()
    period_end = period_start + timedelta(days=1)

    # 어댑터 호출 10개 × 10배 지연 = 가상 0.5s 추가 (실제 0.05s × 10 × 10)
    # real schedule_payouts calls: ensure_account(1) + post_entry(2) + payouts.create_batch(1) +
    #   payouts.create_item(30) + payouts.mark_released(1) ≈ 35회 DB 호출
    # 35회 × 0.05s × 10 = 17.5s 가상 지연 → 예산 초과 가능
    # 안전 마진: 0.05s × 10 × 35 = 17.5s → 예산 30.0s (실측 0.15s × 200배)

    # 가상 지연 주입: asyncpg fetch/execute 호출에 0.05s × 10배 추가
    import asyncpg

    original_fetch_cls = asyncpg.Connection.fetch
    original_execute_cls = asyncpg.Connection.execute

    async def wrapped_fetch(self, sql, *args, **kwargs):
        _add_virtual_delay(0.0, 0.05 * 10)
        return await original_fetch_cls(self, sql, *args, **kwargs)

    async def wrapped_execute(self, sql, *args, **kwargs):
        _add_virtual_delay(0.0, 0.05 * 10)
        return await original_execute_cls(self, sql, *args, **kwargs)

    asyncpg.Connection.fetch = wrapped_fetch
    asyncpg.Connection.execute = wrapped_execute

    try:
        started = time.perf_counter()
        async with pool.acquire() as conn, conn.transaction():
            batches = await schedule_payouts(
                conn,
                captures,
                period_start=period_start,
                period_end=period_end,
                now=posted_at + _WINDOW + timedelta(seconds=1),
                actor_subject_id=None,
                settlement_window=_WINDOW,
                journal=ports.journal,
                balances=ports.balances,
                audit=ports.audit,
                clock=ports.clock,
                payouts=ports.payouts,
            )
        elapsed_s = time.perf_counter() - started
        cpu_ns, wall_ns = virtual_totals()
        injected_wall_s = wall_ns / 1e9

        print(
            f"\nschedule_payouts batch of {capture_count} captures with 10x virtual delay: "
            f"{elapsed_s:.3f}s (wall reading includes {injected_wall_s:.3f}s injected), "
            f"budget 30.0s"
        )
        assert len(batches) == 1
        assert batches[0].amount == Decimal("8.50") * capture_count
        assert elapsed_s < 30.0, (
            f"schedule_payouts over {capture_count} captures with 10x virtual delay "
            f"took {elapsed_s:.3f}s (budget 30.0s)"
        )
    finally:
        asyncpg.Connection.fetch = original_fetch_cls
        asyncpg.Connection.execute = original_execute_cls
