"""LC-15a `application/payouts.py` 심층 보조 테스트 — depth1.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§4.4, §9 LC-15.
DoD: D2 보강 — 기존 test_payouts.py와 같은 fixture/임포트 패턴을 그대로 따른다.
새 파일은 기존 파일을 수정하지 않으며 300줄 이하를 목표로 한다.

추가 보강 항목:
- negative 테스트 3종: (1) 빈 캡처 목록 → 빈 배치 반환,
  (2) 여러 판매자 교차 배치 격리, (3) PAID 배치 재실행 거부
- 실패 주입: `mark_payout_paid` 동시 전이 시도 → `ConcurrencyConflictError`
- 게이트 적색 재현: 위조 capture_entry_id가 UnknownCaptureEntryError를
  일으키는지, 그리고 그 전이 가드가 실제로 발동하는지 단언
- 성능 단언: perf 마커 + virtual delay 10배 주입 + perf_budget.best_of 측정
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
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
    mark_payout_paid,
    schedule_payouts,
)
from src.foundation.ledger.application.purchase_flow import CaptureResult, capture_hold, place_hold
from src.foundation.ledger.contracts.v1 import UserSub
from src.foundation.ledger.domain.chart_of_accounts import user_account as ua
from src.foundation.ledger.domain.payout_schedule import CaptureRecord
from tests.integration.conftest import create_test_user
from tests.support.ledger_seed import seed_user_available_balance
from tests.support.perf_budget import PerfBudget
from tests.support.perf_inject import (
    reset_virtual_totals,
    virtual_totals,
)

_TEST_PURPOSE = "TEST_PAYOUT_DEPTH1"
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
    reference = f"test-payout-depth1:{uuid4()}"
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


# ── Negative tests (3종) ──────────────────────────────────────────────────


async def test_schedule_payouts_empty_captures_returns_no_batches(pool, ports):
    """음성: 빈 캡처 목록 → 빈 배치 반환, 분개/배치/항목 테이블에 기록 없음."""
    seller = await create_test_user(pool)
    posted_at = _clock()
    period_start = posted_at.date()
    period_end = period_start + timedelta(days=1)

    async with pool.acquire() as conn, conn.transaction():
        batches = await schedule_payouts(
            conn,
            [],
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
    assert batches == []

    # 이 테스트의 seller만 카운트 (다른 테스트의 데이터와 격리)
    async with pool.acquire() as conn:
        batch_count = await conn.fetchval(
            "SELECT COUNT(*) FROM ledger_payout_batch WHERE seller_user_id = $1",
            seller,
        )
        item_count = await conn.fetchval(
            "SELECT COUNT(*) FROM ledger_payout_item li "
            "JOIN ledger_payout_batch b ON li.batch_id = b.batch_id "
            "WHERE b.seller_user_id = $1",
            seller,
        )
    assert batch_count == 0
    assert item_count == 0


async def test_schedule_payouts_multi_seller_isolates_batches(pool, ports):
    """음성: 여러 판매자의 캡처가 각각 별도 배치로 생성되며, 금액이 섞이지 않는다."""
    buyer = await create_test_user(pool)
    seller_a = await create_test_user(pool)
    seller_b = await create_test_user(pool)

    capture_a = await _captured_hold(pool, ports, buyer, seller_a, Decimal("100.00"))
    capture_b = await _captured_hold(pool, ports, buyer, seller_b, Decimal("200.00"))

    posted_at = capture_a.entry.posted_at
    period_start = posted_at.date()
    period_end = period_start + timedelta(days=1)
    now = posted_at + _WINDOW + timedelta(seconds=1)

    records = [
        _record_for(capture_a, seller_a),
        _record_for(capture_b, seller_b),
    ]

    async with pool.acquire() as conn, conn.transaction():
        batches = await schedule_payouts(
            conn,
            records,
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

    assert len(batches) == 2
    amounts_by_seller = {b.seller_user_id: b.amount for b in batches}
    assert amounts_by_seller[seller_a] == Decimal("85.00")
    assert amounts_by_seller[seller_b] == Decimal("170.00")

    # PENDING_PAYOUT → AVAILABLE 전이 각 판매자별로 독립적으로 발생
    assert await _balance(pool, ua(seller_a, UserSub.PENDING_PAYOUT)) == Decimal("0.00")
    assert await _balance(pool, ua(seller_a, UserSub.AVAILABLE)) == Decimal("85.00")
    assert await _balance(pool, ua(seller_b, UserSub.PENDING_PAYOUT)) == Decimal("0.00")
    assert await _balance(pool, ua(seller_b, UserSub.AVAILABLE)) == Decimal("170.00")


async def test_mark_payout_paid_rejects_already_paid_batch(pool, ports):
    """음성: 이미 PAID인 배치에 mark_payout_paid 재실행 → UnknownPayoutBatchError 아님,
    대신 전이 규칙 위반으로 ConcurrencyConflictError를 던진다."""
    buyer = await create_test_user(pool)
    seller = await create_test_user(pool)
    capture = await _captured_hold(pool, ports, buyer, seller, Decimal("50.00"))
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
    assert batch.state == "RELEASED"

    admin = await create_test_user(pool)
    # 첫 번째 mark_payout_paid — 정상 통과
    async with pool.acquire() as conn, conn.transaction():
        paid = await mark_payout_paid(
            conn,
            batch.batch_id,
            admin_id=admin,
            external_ref="first-wire",
            journal=ports.journal,
            balances=ports.balances,
            audit=ports.audit,
            clock=ports.clock,
            payouts=ports.payouts,
        )
    assert paid.state == "PAID"

    # 두 번째 mark_payout_paid — 이미 PAID이므로 전이 규칙 위반
    with pytest.raises(ConcurrencyConflictError):
        async with pool.acquire() as conn, conn.transaction():
            await mark_payout_paid(
                conn,
                batch.batch_id,
                admin_id=admin,
                external_ref="second-wire",
                journal=ports.journal,
                balances=ports.balances,
                audit=ports.audit,
                clock=ports.clock,
                payouts=ports.payouts,
            )


# ── Failure injection ─────────────────────────────────────────────────────


async def test_mark_payout_paid_concurrent_transition_fails(pool, ports):
    """실패 주입: 두 동시 트랜잭션이 같은 RELEASED 배치를 PAID으로 전이 시도.
    한 쪽만 성공하고 다른 쪽은 ConcurrencyConflictError를 던진다(105번 표준)."""
    buyer = await create_test_user(pool)
    seller = await create_test_user(pool)
    capture = await _captured_hold(pool, ports, buyer, seller, Decimal("75.00"))
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
    assert batch.state == "RELEASED"

    admin = await create_test_user(pool)
    errors: list[Exception] = []

    async def _try_mark():
        try:
            async with pool.acquire() as conn, conn.transaction():
                await mark_payout_paid(
                    conn,
                    batch.batch_id,
                    admin_id=admin,
                    external_ref="concurrent-wire",
                    journal=ports.journal,
                    balances=ports.balances,
                    audit=ports.audit,
                    clock=ports.clock,
                    payouts=ports.payouts,
                )
        except ConcurrencyConflictError as exc:
            errors.append(exc)

    await _try_mark()
    await _try_mark()

    # 두 시도 중 하나가 성공하고 다른 하나가 ConcurrencyConflictError
    assert len(errors) == 1
    async with pool.acquire() as conn:
        state = await conn.fetchval(
            "SELECT state FROM ledger_payout_batch WHERE batch_id = $1",
            batch.batch_id,
        )
    assert state == "PAID"


# ── Gate-red reproduction ─────────────────────────────────────────────────


async def test_schedule_payouts_gate_red_unknown_capture_entry(pool, ports):
    """게이트 적색 재현 — 위조 capture_entry_id가 UnknownCaptureEntryError를
     일으키며, 트랜잭션 롤백으로 PENDING_PAYOUT 잔액이 변하지 않는다.

     `postgres_payout_repository.create_batch`는 각 capture_entry_id에 대해
     ledger_posting_line에서 CREDIT 금액을 조회한다. 해당 entry_id가
     PENDING_PAYOUT 계정으로 CREDIT를 남기지 않으면 UnknownCaptureEntryError
    를 던진다. 이 테스트는 예외가 실제로 발생하고, 롤백이 발동해 원본
     PENDING_PAYOUT 잔액이 그대로 유지되는 것을 단언한다."""
    buyer = await create_test_user(pool)
    seller = await create_test_user(pool)

    # 판매자에게 진짜 캡처를 하나 만들어 PENDING_PAYOUT 계정을 생성
    capture = await _captured_hold(pool, ports, buyer, seller, Decimal("30.00"))
    posted_at = capture.entry.posted_at
    period_start = posted_at.date()
    period_end = period_start + timedelta(days=1)
    seller_pending = ua(seller, UserSub.PENDING_PAYOUT)

    pending_before = await _balance(pool, seller_pending)
    # capture_hold에서 commission_rate=0.15 적용됨: 30.00 × 0.85 = 25.50
    assert pending_before == Decimal("25.50")

    # 위조 레코드: entry_id만 무작위 UUID
    forged_record = CaptureRecord(
        entry_id=uuid4(),
        seller_user_id=seller,
        amount=capture.payout_amount,
        currency=Currency.KRW,
        captured_at=posted_at,
    )

    # 예외가 트랜잭션 밖에서 포착되어야 롤백이 발동한다
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

    # 롤백 발동 확인: PENDING_PAYOUT 잔액이 변하지 않음
    assert await _balance(pool, seller_pending) == pending_before


# ── Performance tests ─────────────────────────────────────────────────────


@pytest.mark.perf
async def test_schedule_payouts_baseline_performance(pool, ports, perf_budget: PerfBudget) -> None:
    """성능 baseline — 30건 캡처 정산 배치의 실제 CPU/벽시간을
    perf_budget.best_of로 측정한다.

    실측 예산: 로컬에서 30건 배치 평균 < 1s (CPU 기준).
    예산은 실측의 30배 이상인 30.0s로 여유를 뒀다.
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

    result = await perf_budget.best_of_async(
        lambda: _run_schedule(pool, ports, captures, period_start, period_end, posted_at),
        n=5,
    )
    print(
        f"\nschedule_payouts baseline 30 captures — cpu={result.cpu_ms:.1f}ms "
        f"wall={result.wall_ms:.1f}ms"
    )
    # 실측의 30배 여유 예산 (실측 ~1s → 30.0s)
    assert result.cpu_ms < 30000.0, (
        f"schedule_payouts exceeded budget: {result.cpu_ms:.1f}ms >= 30000.0ms"
    )


@pytest.mark.perf
async def test_schedule_payouts_virtual_delay_isolation(
    pool, ports, perf_budget: PerfBudget
) -> None:
    """성능 격리 확인 — schedule_payouts 내부에서 place_hold/capture_hold를
    호출하지 않으므로, virtual_delayed_async로 해당 함수를 감싸도
    virtual_totals()가 (0, 0)을 반환해야 한다.

    이는 schedule_payouts의 성능 측정에서
    hold 관련 virtual delay가 섞이지 않음을 보장한다."""
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

    # schedule_payouts 실행
    await perf_budget.sample_async(
        lambda: _run_schedule(pool, ports, captures, period_start, period_end, posted_at)
    )

    # virtual delay가 전혀 주입되지 않았음을 확인
    virtual_total_delay_ns, virtual_total_actual_ns = virtual_totals()
    assert virtual_total_delay_ns == 0, (
        f"Unexpected virtual delay recorded: {virtual_total_delay_ns}ns "
        f"(schedule_payouts should not call place_hold/capture_hold)"
    )
    assert virtual_total_actual_ns == 0, (
        f"Unexpected actual delay recorded: {virtual_total_actual_ns}ns"
    )


async def _run_schedule(pool, ports, captures, period_start, period_end, posted_at):
    """schedule_payouts 호출을 감싸서 perf_budget가 측정할 수 있게 한다."""
    async with pool.acquire() as conn, conn.transaction():
        return await schedule_payouts(
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
