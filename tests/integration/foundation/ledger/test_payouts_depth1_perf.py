"""LC-15a `application/payouts.py` 심층 보조 테스트 — depth1 성능.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§4.4, §9 LC-15.
DoD: D2 보강 — test_payouts_depth1.py와 같은 fixture/임포트 패턴을 그대로 따른다.
성능 테스트는 별도 파일로 분리해 원본 파일의 500줄 경계를 유지한다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from src.foundation.ledger.adapters.postgres_audit_event_repository import (
    PostgresAuditEventRepository,
)

from src.foundation.ledger.adapters.postgres_balance_repository import PostgresBalanceRepository
from src.foundation.ledger.adapters.postgres_hold_repository import PostgresHoldRepository
from src.foundation.ledger.adapters.postgres_journal_repository import PostgresJournalRepository
from src.foundation.ledger.adapters.postgres_payout_repository import (
    PostgresPayoutRepository,
)
from src.foundation.ledger.application.payouts import schedule_payouts
from src.foundation.ledger.application.purchase_flow import CaptureResult, capture_hold, place_hold
from src.foundation.ledger.contracts.v1 import UserSub
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
        )
    return capture


def _record_for(capture: CaptureResult, seller: UserSub) -> CaptureRecord:
    return CaptureRecord(
        sale_id=capture.entry.sale_id,
        sale_date=capture.entry.posted_at.date(),
        seller_id=seller.user_id,
        seller_tax_id=seller.tax_id or "",
        capture_entry_id=capture.entry.entry_id,
        captured_at=capture.entry.posted_at,
        gross_amount=capture.entry.amount,
        commission_rate=Decimal("0.0025"),
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
