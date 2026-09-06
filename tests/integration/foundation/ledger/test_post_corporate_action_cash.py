"""LA-25b `application/post_corporate_action_cash.py` 통합테스트 — 실 DB
(TEST_DATABASE_URL) 대상.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§4.4, §9 LA-25b,
ADR-2026-09-06-G §9.
DoD(task-1753): amount>0인 CASH_DIVIDEND가 보유자 AVAILABLE에 전기,
(instrument, ex_date, holder)에 멱등, 중복 호출 시 분개 1건 유지, 시산표 합 0
유지 + negative(action_type 불일치, amount<=0 미전기)."""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.ledger.adapters.postgres_balance_repository import PostgresBalanceRepository
from src.foundation.ledger.adapters.postgres_journal_repository import PostgresJournalRepository
from src.foundation.ledger.application.post_corporate_action_cash import (
    UnsupportedCorporateActionError,
    post_corporate_action_cash,
)
from src.foundation.ledger.contracts.v1 import UserSub
from src.foundation.ledger.domain.chart_of_accounts import PLATFORM_CASH_CLEARING
from src.foundation.ledger.domain.chart_of_accounts import user_account as ua
from src.foundation.ledger.domain.idempotency import IdempotencyDigestMismatchError
from src.foundation.market_data.contracts.v1 import CorporateAction
from tests.integration.conftest import create_test_user


def _clock() -> datetime:
    return datetime.now(timezone.utc)


class _RealPorts:
    def __init__(self, pool) -> None:
        self.journal = PostgresJournalRepository(pool)
        self.balances = PostgresBalanceRepository(pool)
        self.audit = PostgresAuditEventRepository(pool)
        self.clock = _clock


@pytest.fixture
def ports(pool):
    return _RealPorts(pool)


def _dividend_action(
    *, instrument_id: UUID | None = None, ex_date: date | None = None
) -> CorporateAction:
    return CorporateAction(
        action_type="CASH_DIVIDEND",
        instrument_id=instrument_id or uuid4(),
        ex_date=ex_date or date(2026, 9, 7),
        ratio=Decimal("1"),
        cash_amount=Decimal("500.00"),
        source_ref="test:dividend",
    )


async def _balance(pool, account_code: str) -> Decimal:
    async with pool.acquire() as conn:
        value = await conn.fetchval(
            "SELECT lb.balance FROM ledger_balance lb JOIN ledger_account la "
            "ON la.account_id = lb.account_id WHERE la.account_code = $1",
            account_code,
        )
    return value if value is not None else Decimal("0")


async def _entry_lines(pool, entry_id) -> list[tuple[str, Decimal]]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT side, amount FROM ledger_posting_line WHERE entry_id = $1", entry_id
        )
    return [(row["side"], row["amount"]) for row in rows]


async def test_posts_cash_dividend_to_holder_available(pool, ports):
    holder = await create_test_user(pool)
    action = _dividend_action()
    amount = Decimal("500.00")
    cash_clearing_before = await _balance(pool, PLATFORM_CASH_CLEARING)

    async with pool.acquire() as conn, conn.transaction():
        entry = await post_corporate_action_cash(
            conn, action, holder_id=holder, amount=amount, admin_id=None,
            trace_id=uuid4(), journal=ports.journal, balances=ports.balances,
            audit=ports.audit, clock=ports.clock,
        )

    assert entry is not None
    assert entry.replayed is False
    lines = await _entry_lines(pool, entry.entry_id)
    debit_total = sum((a for side, a in lines if side == "DEBIT"), Decimal("0"))
    credit_total = sum((a for side, a in lines if side == "CREDIT"), Decimal("0"))
    assert debit_total == credit_total == amount  # 시산표 Σ차=Σ대(직접 단언)

    assert await _balance(pool, ua(holder, UserSub.AVAILABLE)) == amount
    cash_clearing_after = await _balance(pool, PLATFORM_CASH_CLEARING)
    # 배당 현금은 TOPUP_CONFIRMED와 같은 모양으로 외부에서 들어온다 — 자산
    # 계정(PLATFORM:CASH_CLEARING)이 debit으로 증가한다(§4.4 부호 규약).
    assert cash_clearing_after - cash_clearing_before == amount


async def test_duplicate_call_keeps_single_journal_entry(pool, ports):
    holder = await create_test_user(pool)
    action = _dividend_action()
    amount = Decimal("120.00")

    async def _post():
        async with pool.acquire() as conn, conn.transaction():
            return await post_corporate_action_cash(
                conn, action, holder_id=holder, amount=amount, admin_id=None,
                trace_id=uuid4(), journal=ports.journal, balances=ports.balances,
                audit=ports.audit, clock=ports.clock,
            )

    first = await _post()
    second = await _post()

    assert first is not None and second is not None
    assert first.replayed is False
    assert second.replayed is True
    assert second.entry_id == first.entry_id

    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM ledger_journal_entry WHERE event_ref = $1",
            first.event_ref,
        )
    assert count == 1
    # REPLAY라 두 번째 호출은 잔액을 다시 건드리지 않았다 — 최초 1회분만 반영.
    assert await _balance(pool, ua(holder, UserSub.AVAILABLE)) == amount


async def test_different_holder_same_action_is_independent(pool, ports):
    """멱등 범위는 (instrument, ex_date, holder) — 보유자가 다르면 별개
    분개여야 한다."""
    holder_a, holder_b = await create_test_user(pool), await create_test_user(pool)
    action = _dividend_action()

    async def _post(holder: UUID, amount: Decimal):
        async with pool.acquire() as conn, conn.transaction():
            return await post_corporate_action_cash(
                conn, action, holder_id=holder, amount=amount, admin_id=None,
                trace_id=uuid4(), journal=ports.journal, balances=ports.balances,
                audit=ports.audit, clock=ports.clock,
            )

    entry_a = await _post(holder_a, Decimal("300.00"))
    entry_b = await _post(holder_b, Decimal("70.00"))

    assert entry_a is not None and entry_b is not None
    assert entry_a.entry_id != entry_b.entry_id
    assert await _balance(pool, ua(holder_a, UserSub.AVAILABLE)) == Decimal("300.00")
    assert await _balance(pool, ua(holder_b, UserSub.AVAILABLE)) == Decimal("70.00")


async def test_amount_changed_between_calls_is_digest_mismatch(pool, ports):
    """같은 (instrument, ex_date, holder)로 다른 금액이 재전송되면 거부
    (negative case) — 조용히 덮어쓰지 않는다(fail-closed)."""
    holder = await create_test_user(pool)
    action = _dividend_action()

    async with pool.acquire() as conn, conn.transaction():
        await post_corporate_action_cash(
            conn, action, holder_id=holder, amount=Decimal("100.00"), admin_id=None,
            trace_id=uuid4(), journal=ports.journal, balances=ports.balances,
            audit=ports.audit, clock=ports.clock,
        )

    with pytest.raises(IdempotencyDigestMismatchError):
        async with pool.acquire() as conn, conn.transaction():
            await post_corporate_action_cash(
                conn, action, holder_id=holder, amount=Decimal("999.00"), admin_id=None,
                trace_id=uuid4(), journal=ports.journal, balances=ports.balances,
                audit=ports.audit, clock=ports.clock,
            )


async def test_non_cash_dividend_action_type_is_rejected(pool, ports):
    """negative case — SPLIT 등 다른 기업행위 타입은 이 함수의 책임이 아니다."""
    holder = await create_test_user(pool)
    split_action = CorporateAction(
        action_type="SPLIT",
        instrument_id=uuid4(),
        ex_date=date(2026, 9, 7),
        ratio=Decimal("2"),
        source_ref="test:split",
    )

    with pytest.raises(UnsupportedCorporateActionError):
        async with pool.acquire() as conn, conn.transaction():
            await post_corporate_action_cash(
                conn, split_action, holder_id=holder, amount=Decimal("100.00"), admin_id=None,
                trace_id=uuid4(), journal=ports.journal, balances=ports.balances,
                audit=ports.audit, clock=ports.clock,
            )


async def test_zero_amount_does_not_post(pool, ports):
    """negative case — amount<=0은 분개를 만들지 않는다(§3.3 PostingLine.amount>0)."""
    holder = await create_test_user(pool)
    action = _dividend_action()

    async with pool.acquire() as conn, conn.transaction():
        result = await post_corporate_action_cash(
            conn, action, holder_id=holder, amount=Decimal("0.00"), admin_id=None,
            trace_id=uuid4(), journal=ports.journal, balances=ports.balances,
            audit=ports.audit, clock=ports.clock,
        )

    assert result is None
    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM ledger_journal_entry WHERE event_ref LIKE $1",
            f"corp_action_cash:{action.instrument_id}:%",
        )
    assert count == 0
