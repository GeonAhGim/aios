"""LC-16 `LedgerIntegrityScheduler.run_payout_once` 통합테스트 — 실 DB
(TEST_DATABASE_URL) 대상.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§7, §9 LC-16.
DoD(task-1761) — 같은 정산창(같은 `now`가 만드는 같은 `period_end`)에서 배치를
2회 실행해도 정산 행(`ledger_payout_batch`/`ledger_payout_item`)이 중복
생성되지 않는다. `scheduler.py::_fetch_payout_capture_candidates`의
`NOT EXISTS(ledger_payout_item)` 후보 배제가 실제로 작동해 두 번째 실행은
MY SELLER 몫의 후보가 더 없어 빈 배치 목록을 돌려줘야 한다.

`_fetch_payout_capture_candidates`는 전역 스캔이라(모듈 docstring —
"기간과 무관하게 넓게 모은다"), 이 디렉터리의 다른 리프가 남긴, 아직
정산되지 않은 `HOLD_CAPTURED`도 같은 배치 실행에 함께 걸린다. 그 중 일부는
그 사이 `REFUND`(R1: 정산창 내 환불, seller `PENDING_PAYOUT`에서 직접 차변,
`application/refund.py` 참조)로 이미 차감되어, 후보 목록이 기대하는 금액과
실제 잔액이 어긋난 상태로 남을 수 있다 — capture-refund 간 SQL 조인 키가
없어(각각 독립된 event_ref) `_fetch_payout_capture_candidates`가 이 경우를
아직 걸러내지 못한다(별도 결함, 이 leaf의 최소수정 범위 밖). 그런 잔여
후보가 하나라도 있으면 `schedule_payouts`가 단일 트랜잭션이라 배치 전체가
`InsufficientAvailableError`로 롤백돼 이 테스트가 아니라 무관한 다른
테스트의 잔여물 때문에 깨진다. `_quarantine_unsettleable_candidates`가 그런
잔여 후보를 매 실행 전에 걸러(`ledger_payout_item` 마커만 남기고 실제
잔액은 건드리지 않음) 이 테스트가 그 오염과 무관하게 결정적으로 동작하게
한다.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from src.core.observability.metrics_registry import MetricsRegistry
from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.ledger.adapters.postgres_balance_repository import PostgresBalanceRepository
from src.foundation.ledger.adapters.postgres_hold_repository import PostgresHoldRepository
from src.foundation.ledger.adapters.postgres_journal_repository import PostgresJournalRepository
from src.foundation.ledger.adapters.postgres_payout_repository import PostgresPayoutRepository
from src.foundation.ledger.application.payouts import DEFAULT_SETTLEMENT_WINDOW
from src.foundation.ledger.application.purchase_flow import capture_hold, place_hold
from src.foundation.ledger.application.scheduler import LedgerIntegrityScheduler
from src.foundation.ledger.contracts.v1 import UserSub
from src.foundation.ledger.domain.chart_of_accounts import user_account as ua
from tests.integration.conftest import create_test_user
from tests.support.ledger_seed import seed_user_available_balance

_TEST_PURPOSE = "TEST_SCHEDULER_PAYOUT"


async def _quarantine_unsettleable_candidates(pool, payouts: PostgresPayoutRepository) -> None:
    """이 디렉터리의 다른 leaf가 남긴, 이미 REFUND(R1)로 잔액이 빠져나가
    `schedule_payouts`가 절대 성공적으로 정산할 수 없는 `HOLD_CAPTURED`
    후보를 찾아 `ledger_payout_item` 마커만 남긴다(state=SCHEDULED,
    `release_entry_id=None` — 실제로 지급됐다고 주장하지 않는다). 이 테스트
    파일 스코프의 방어적 정리일 뿐, `scheduler.py` 자체의 (아직 해소되지
    않은) 후보 배제 결함을 고치는 것은 아니다."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT je.entry_id, la.account_code, pl.amount "
            "FROM ledger_journal_entry je "
            "JOIN ledger_posting_line pl ON pl.entry_id = je.entry_id AND pl.side = 'CREDIT' "
            "JOIN ledger_account la ON la.account_id = pl.account_id "
            "WHERE je.event_type = 'HOLD_CAPTURED' "
            "AND la.account_code LIKE 'USER:%:PENDING_PAYOUT' "
            "AND NOT EXISTS ("
            "  SELECT 1 FROM ledger_payout_item lpi WHERE lpi.capture_entry_id = je.entry_id"
            ") "
            "AND NOT EXISTS ("
            "  SELECT 1 FROM ledger_hold lh "
            "  JOIN ledger_journal_entry rel "
            "    ON rel.event_ref = lh.reference || ':release' "
            "    AND rel.event_type = 'PAYOUT_RELEASE' "
            "  WHERE lh.settled_entry_id = je.entry_id"
            ") "
            "AND NOT EXISTS ("
            "  SELECT 1 FROM ledger_journal_entry same_ref "
            "  WHERE same_ref.event_ref = je.event_ref AND same_ref.event_type = 'PAYOUT_RELEASE'"
            ")"
        )
        by_seller: dict[UUID, list[tuple[UUID, Decimal]]] = {}
        for row in rows:
            seller = UUID(row["account_code"].split(":")[1])
            by_seller.setdefault(seller, []).append((row["entry_id"], row["amount"]))

        for seller, items in by_seller.items():
            total = sum((amount for _, amount in items), Decimal("0"))
            current_balance = await conn.fetchval(
                "SELECT balance FROM ledger_balance lb JOIN ledger_account la "
                "ON la.account_id = lb.account_id WHERE la.account_code = $1",
                ua(seller, UserSub.PENDING_PAYOUT),
            )
            if current_balance is not None and total <= current_balance:
                continue  # 실제로 정산 가능한 후보 — 손대지 않는다.
            async with conn.transaction():
                await payouts.create_batch(
                    conn,
                    batch_id=uuid4(),
                    seller_user_id=seller,
                    period_start=date(1970, 1, 1),
                    period_end=date(1970, 1, 2),
                    amount=total,
                    capture_entry_ids=[entry_id for entry_id, _ in items],
                    release_entry_id=None,
                )


def _clock() -> datetime:
    return datetime.now(timezone.utc)


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


@pytest.fixture
def scheduler(pool):
    return LedgerIntegrityScheduler(
        pool,
        journal=PostgresJournalRepository(pool),
        balances=PostgresBalanceRepository(pool),
        audit=PostgresAuditEventRepository(pool),
        registry=MetricsRegistry(),
        payouts=PostgresPayoutRepository(pool),
    )


async def test_run_payout_once_twice_in_same_window_does_not_duplicate_rows(pool, scheduler):
    await _quarantine_unsettleable_candidates(pool, PostgresPayoutRepository(pool))

    buyer = await create_test_user(pool)
    seller = await create_test_user(pool)
    price = Decimal("100.00")
    reference = f"test-scheduler-payout:{uuid4()}"
    await _seed_available(pool, buyer, price)

    holds = PostgresHoldRepository(pool)
    journal = PostgresJournalRepository(pool)
    balances = PostgresBalanceRepository(pool)
    audit = PostgresAuditEventRepository(pool)
    captured_at = _clock()

    async with pool.acquire() as conn, conn.transaction():
        hold = await place_hold(
            conn, buyer_id=buyer, amount=price, purpose=_TEST_PURPOSE, reference=reference,
            expires_at=captured_at + timedelta(minutes=15), actor_subject_id=buyer,
            trace_id=uuid4(), journal=journal, balances=balances, audit=audit,
            clock=_clock, holds=holds,
        )
        await capture_hold(
            conn, hold, seller_id=seller, commission_rate=Decimal("0.15"),
            actor_subject_id=buyer, trace_id=uuid4(), now=captured_at,
            journal=journal, balances=balances, audit=audit, clock=_clock, holds=holds,
        )

    seller_pending = ua(seller, UserSub.PENDING_PAYOUT)
    seller_available = ua(seller, UserSub.AVAILABLE)
    assert await _balance(pool, seller_pending) == Decimal("85.00")

    # 정산창(기본 7일)이 이미 지난 시점으로 스케줄러를 실행 — 같은 날짜가
    # 같은 period_end를 만들어 "같은 창"을 재현한다.
    run_at = captured_at + DEFAULT_SETTLEMENT_WINDOW + timedelta(seconds=1)

    first = await scheduler.run_payout_once(run_at)
    second = await scheduler.run_payout_once(run_at)

    # `run_payout_once`는 전역 스캔이라(모듈 docstring), 같은 배치 실행에
    # 이 디렉터리의 다른(진짜 미정산) leaf 데이터도 함께 걸릴 수 있다 — 그래서
    # 절대 개수가 아니라 MY SELLER 몫만 골라 단언한다.
    mine_first = [b for b in first if b.seller_user_id == seller]
    mine_second = [b for b in second if b.seller_user_id == seller]

    assert len(mine_first) == 1
    assert mine_first[0].state == "RELEASED"
    assert mine_second == []  # 후보가 이미 배치에 편입돼 두 번째 실행은 대상이 없다

    assert await _balance(pool, seller_pending) == Decimal("0.00")
    assert await _balance(pool, seller_available) == Decimal("85.00")  # 중복 지급 없음

    async with pool.acquire() as conn:
        batch_count = await conn.fetchval(
            "SELECT COUNT(*) FROM ledger_payout_batch WHERE seller_user_id = $1", seller
        )
        item_count = await conn.fetchval(
            "SELECT COUNT(*) FROM ledger_payout_item WHERE batch_id = $1", mine_first[0].batch_id
        )
    assert batch_count == 1
    assert item_count == 1
