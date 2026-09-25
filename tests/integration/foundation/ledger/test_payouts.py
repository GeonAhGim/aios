"""LC-15a `application/payouts.py`/`adapters/postgres_payout_repository.py`
통합테스트 — 실 DB(TEST_DATABASE_URL) 대상.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§4.4, §8.2
test_payouts.py, §9 LC-15.
DoD: 홀드 창 경과 후 RELEASE, PAID 후 `PLATFORM:PAYOUT_CLEARING` 증가, 같은
(seller_user_id, period_end) 배치 재실행 멱등 — 세 케이스 전부 실 DB로 단언.

task-2961(DEEPEN, docs/audit/DEPTH_LA_LB_LC.md task-486 행): 원 리프가
negative≥3·failure-injection(재처리 시 `ConcurrencyConflictError`)·replay/
멱등 증명은 갖췄으나 수치 성능 단언과 게이트 적색 재현이 없어 D3 축 하한
(D2 4요건 전부) 미달로 판정됐다. 이 파일에 두 케이스를 보강한다:
`test_schedule_payouts_batch_meets_latency_budget`(수치 성능 단언)과
`test_schedule_payouts_rejects_forged_capture_entry_reference`(게이트 적색
재현 — `postgres_payout_repository.UnknownCaptureEntryError` fail-closed
가드가 실제로 발동함을 실증, 기존 4케이스 중 누구도 이 가드를 밟지 않았다).
"""

from __future__ import annotations

import time
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
from src.foundation.ledger.domain.chart_of_accounts import PLATFORM_PAYOUT_CLEARING
from src.foundation.ledger.domain.chart_of_accounts import user_account as ua
from src.foundation.ledger.domain.payout_schedule import CaptureRecord
from tests.integration.conftest import create_test_user
from tests.support.ledger_seed import seed_user_available_balance

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
    """FA-15a(esc-2115): raw INSERT로 잔액을 직접 심지 않는다 — 실제 충전
    진입점(`post_topup`)을 그대로 태우는 `seed_user_available_balance`로
    TOPUP_CONFIRMED 분개를 남긴다."""
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
    reference = f"test-payout:{uuid4()}"
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
            conn,
            [record],
            period_start=period_start,
            period_end=period_end,
            now=posted_at,
            actor_subject_id=None,
            settlement_window=_WINDOW,
            journal=ports.journal,
            balances=ports.balances,
            audit=ports.audit,
            clock=ports.clock,
            payouts=ports.payouts,
        )
    assert too_early == []
    assert await _balance(pool, seller_pending) == Decimal("85.00")

    async with pool.acquire() as conn, conn.transaction():
        batches = await schedule_payouts(
            conn,
            [record],
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
                conn,
                [record],
                period_start=period_start,
                period_end=period_end,
                now=now,
                actor_subject_id=None,
                settlement_window=_WINDOW,
                journal=ports.journal,
                balances=ports.balances,
                audit=ports.audit,
                clock=ports.clock,
                payouts=ports.payouts,
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
            conn,
            [record],
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
    batch = batches[0]
    seller_available = ua(seller, UserSub.AVAILABLE)
    clearing_before = await _balance(pool, PLATFORM_PAYOUT_CLEARING)
    assert await _balance(pool, seller_available) == Decimal("85.00")

    admin = await create_test_user(pool)
    async with pool.acquire() as conn, conn.transaction():
        paid = await mark_payout_paid(
            conn,
            batch.batch_id,
            admin_id=admin,
            external_ref="test-wire-001",
            journal=ports.journal,
            balances=ports.balances,
            audit=ports.audit,
            clock=ports.clock,
            payouts=ports.payouts,
        )

    assert paid.state == "PAID"
    assert paid.paid_entry_id is not None
    assert await _balance(pool, seller_available) == Decimal("0.00")
    assert await _balance(pool, PLATFORM_PAYOUT_CLEARING) == clearing_before + Decimal("85.00")

    # 이미 PAID인 배치를 다시 확정하려는 시도는 거부(negative case).
    async with pool.acquire() as conn, conn.transaction():
        with pytest.raises(ConcurrencyConflictError):
            await mark_payout_paid(
                conn,
                batch.batch_id,
                admin_id=admin,
                external_ref="test-wire-002",
                journal=ports.journal,
                balances=ports.balances,
                audit=ports.audit,
                clock=ports.clock,
                payouts=ports.payouts,
            )


async def test_mark_payout_paid_unknown_batch_is_rejected(pool, ports):
    admin = await create_test_user(pool)
    async with pool.acquire() as conn, conn.transaction():
        with pytest.raises(UnknownPayoutBatchError):
            await mark_payout_paid(
                conn,
                uuid4(),
                admin_id=admin,
                external_ref="test-wire-unknown",
                journal=ports.journal,
                balances=ports.balances,
                audit=ports.audit,
                clock=ports.clock,
                payouts=ports.payouts,
            )


async def test_schedule_payouts_rejects_forged_capture_entry_reference(pool, ports):
    """게이트 적색 재현 -- `capture_entry_id`가 이 판매자 `PENDING_PAYOUT`
    계정으로 실제 CREDIT을 남긴 분개가 아니면(위조/오배선된 참조) 배치를
    조용히 만들지 않고 `UnknownCaptureEntryError`로 fail-closed 거부한다
    (`postgres_payout_repository.py` 모듈 docstring 계약). 기존 4케이스는
    모두 정직한 `CaptureRecord`만 다뤄 이 가드를 한 번도 밟지 않았다 --
    판매자에게 진짜 캡처 하나를 먼저 태워 `PENDING_PAYOUT` 계정 자체는
    존재하게 한 뒤, `entry_id`만 무관한 값으로 위조해 그 가드를 정확히
    겨냥한다."""
    buyer = await create_test_user(pool)
    seller = await create_test_user(pool)
    capture = await _captured_hold(pool, ports, buyer, seller, Decimal("30.00"))
    posted_at = capture.entry.posted_at
    period_start = posted_at.date()
    period_end = period_start + timedelta(days=1)
    seller_pending = ua(seller, UserSub.PENDING_PAYOUT)

    forged_record = CaptureRecord(
        entry_id=uuid4(),
        seller_user_id=seller,
        amount=capture.payout_amount,
        currency=Currency.KRW,
        captured_at=posted_at,
    )

    # pytest.raises가 `conn.transaction()` 블록 *밖에서* 예외를 받아야
    # 롤백이 실제로 발동한다 -- 안에서 잡으면 asyncpg는 예외 없이 정상
    # 종료로 보고 커밋해 버린다(그 자체가 이 가드의 fail-closed 여부를
    # 가리는 함정이라 일부러 바깥에 둔다).
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

    # 거부된 트랜잭션은 롤백되어 판매자 PENDING_PAYOUT도 그대로다(부분 반영 없음).
    assert await _balance(pool, seller_pending) == Decimal("25.50")


@pytest.mark.perf
async def test_schedule_payouts_batch_meets_latency_budget(pool, ports):
    """수치 성능 단언 -- 한 판매자에게 30건의 캡처가 몰린 정산 배치 하나를
    실 DB로 만드는 데 걸리는 시간에 예산을 둔다. `create_batch`가 캡처마다
    순차 SELECT+INSERT 2회씩(어댑터 docstring, 드리프트 방지를 위해 새로
    계산하지 않고 기존 분개에서 읽기만 함)을 쓰므로 항목 수에 선형이다 --
    이 예산은 그 선형 비용이 조용히 폭증(예: N+1이 N*M으로 퇴행)하는 것만
    잡는 느슨한 상한이며 절대 하한 최적화를 요구하지 않는다."""
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

    print(
        f"\nschedule_payouts batch of {capture_count} captures took {elapsed_s:.3f}s (budget 3.0s)"
    )
    assert len(batches) == 1
    assert batches[0].amount == Decimal("8.50") * capture_count
    assert elapsed_s < 3.0, (
        f"schedule_payouts over {capture_count} captures took {elapsed_s:.3f}s (budget 3.0s)"
    )
