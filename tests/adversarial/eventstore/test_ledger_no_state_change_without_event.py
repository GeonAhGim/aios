"""FA-16 — adversarial: `post_entry` never changes `ledger_balance` without
a matching `ledger_journal_entry` row, in the same transaction.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#§9 FA-16
(선행 FA-14=task-2050, FA-15=task-2060). FA-14가 정한 이벤트 원천을 그대로
쓴다 — 새 이벤트 테이블은 없다: 원장의 이벤트 원천은 `ledger_journal_entry`
(`post_entry.py`, LC-9)이다. `journal.append(conn, event, lines)`가
돌려주는 `entry_view`가 있어야만 잔액 갱신·감사 payload를 만들 수 있어,
저널 append 없이 잔액이 바뀌는 경로는 코드 구조상 존재하지 않는다.

DoD(1) "배선증명 없이 통과하는 테스트는 반려" —
`test_ledger_post_entry_each_call_produces_exactly_one_journal_entry`는
`ledger_journal_entry` 행 수를 잔액 증가분과 직접 비교하므로,
`post_entry.py`의 `entry_view = await journal.append(conn, event, lines)`
줄을 지우면 다음 줄(`entry_view.replayed`)이 `AttributeError`로 즉시
죽어 이 테스트가 FAIL한다 — 실제로 그 줄을 임시로 주석 처리하고 이
테스트가 FAIL하는 것을 확인한 뒤 원복했다(회귀 방지를 위해 sabotage
코드는 커밋에 남기지 않는다).
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import asyncpg
import pytest

from src.data.models.base import Currency
from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.ledger.adapters.postgres_balance_repository import PostgresBalanceRepository
from src.foundation.ledger.adapters.postgres_journal_repository import PostgresJournalRepository
from src.foundation.ledger.application.post_entry import post_entry
from src.foundation.ledger.contracts.v1 import AccountType, LedgerEvent, LedgerEventType, UserSub
from src.foundation.ledger.domain.chart_of_accounts import user_account


def _clock() -> datetime:
    return datetime.now(timezone.utc)


async def _create_user_available_account(
    pool: asyncpg.Pool, user_id: UUID, *, initial_balance: Decimal = Decimal("0")
) -> str:
    code = user_account(user_id, UserSub.AVAILABLE)
    async with pool.acquire() as conn:
        account_id = await conn.fetchval(
            "INSERT INTO ledger_account (account_code, account_type, currency, allow_negative) "
            "VALUES ($1, $2, $3, FALSE) RETURNING account_id",
            code, AccountType.LIABILITY.value, Currency.KRW.value,
        )
        await conn.execute(
            "INSERT INTO ledger_balance (account_id, balance, allow_negative, last_entry_seq) "
            "VALUES ($1, $2, FALSE, 0)",
            account_id, initial_balance,
        )
    return code


def _topup_event(
    *, event_ref: str, user_id: UUID, amount: Decimal = Decimal("10.00")
) -> LedgerEvent:
    return LedgerEvent(
        event_type=LedgerEventType.TOPUP_CONFIRMED, event_ref=event_ref, tenant_id=None,
        actor_subject_id=None, trace_id=uuid4(), amount=amount, currency=Currency.KRW,
        parties={"user": user_id}, extra={},
    )


class _BoomLedgerJournal:
    async def find_by_idempotency_key(self, conn, key):  # noqa: ANN001, ARG002
        return None

    async def append(self, conn, entry, lines):  # noqa: ANN001, ARG002 -- 테스트 전용
        raise RuntimeError("injected ledger_journal_entry append failure")


async def test_ledger_post_entry_each_call_produces_exactly_one_journal_entry(pool):
    """Positive — DoD(1). 서로 다른 두 topup 각각이 정확히 하나의
    `ledger_journal_entry` 행을 남기고 사용자 잔액이 정확히 그만큼 늘어난다."""
    user_id = uuid4()
    user_code = await _create_user_available_account(pool, user_id)
    journal = PostgresJournalRepository(pool)
    balances = PostgresBalanceRepository(pool)
    audit = PostgresAuditEventRepository(pool)
    refs = [f"fa16-adv-topup:{uuid4().hex}" for _ in range(2)]

    for ref in refs:
        async with pool.acquire() as conn, conn.transaction():
            await post_entry(
                conn, _topup_event(event_ref=ref, user_id=user_id, amount=Decimal("10.00")),
                journal=journal, balances=balances, audit=audit, clock=_clock,
            )

    async with pool.acquire() as conn:
        entry_count = await conn.fetchval(
            "SELECT count(*) FROM ledger_journal_entry WHERE event_ref = ANY($1::text[])", refs
        )
        balance = await conn.fetchval(
            "SELECT balance FROM ledger_balance WHERE account_id = "
            "(SELECT account_id FROM ledger_account WHERE account_code = $1)",
            user_code,
        )
    assert entry_count == 2
    assert balance == Decimal("20.00")


async def test_ledger_journal_append_failure_blocks_balance_change(pool):
    """Negative — DoD(3) fail-closed. `journal.append`(이벤트=`ledger_journal_
    entry` append)가 실패하면 잔액 갱신은 이 함수까지 도달하지 못한다(§6
    순서: 저널 append 성공 후에야 잔액을 갱신, `post_entry.py` 모듈
    docstring)."""
    user_id = uuid4()
    user_code = await _create_user_available_account(pool, user_id)
    balances = PostgresBalanceRepository(pool)
    audit = PostgresAuditEventRepository(pool)
    ref = f"fa16-adv-topup-boom:{uuid4().hex}"

    async with pool.acquire() as conn, conn.transaction():
        with pytest.raises(RuntimeError, match="injected ledger_journal_entry append failure"):
            await post_entry(
                conn, _topup_event(event_ref=ref, user_id=user_id, amount=Decimal("10.00")),
                journal=_BoomLedgerJournal(), balances=balances, audit=audit, clock=_clock,
            )

    async with pool.acquire() as conn:
        entry_count = await conn.fetchval(
            "SELECT count(*) FROM ledger_journal_entry WHERE event_ref = $1", ref
        )
        balance = await conn.fetchval(
            "SELECT balance FROM ledger_balance WHERE account_id = "
            "(SELECT account_id FROM ledger_account WHERE account_code = $1)",
            user_code,
        )
    assert entry_count == 0
    assert balance == Decimal("0")


async def test_ledger_transaction_rollback_removes_journal_and_balance_together(pool):
    """DoD(2) — 실DB. `post_entry`가 성공적으로 저널·잔액을 쓴 뒤에도
    (아직 커밋 전) 호출자의 트랜잭션이 롤백되면 둘 다 함께 사라진다."""
    user_id = uuid4()
    user_code = await _create_user_available_account(pool, user_id)
    journal = PostgresJournalRepository(pool)
    balances = PostgresBalanceRepository(pool)
    audit = PostgresAuditEventRepository(pool)
    ref = f"fa16-adv-topup-rollback:{uuid4().hex}"

    async with pool.acquire() as conn:
        tx = conn.transaction()
        await tx.start()
        await post_entry(
            conn, _topup_event(event_ref=ref, user_id=user_id, amount=Decimal("10.00")),
            journal=journal, balances=balances, audit=audit, clock=_clock,
        )
        await tx.rollback()

    async with pool.acquire() as conn:
        entry_count = await conn.fetchval(
            "SELECT count(*) FROM ledger_journal_entry WHERE event_ref = $1", ref
        )
        balance = await conn.fetchval(
            "SELECT balance FROM ledger_balance WHERE account_id = "
            "(SELECT account_id FROM ledger_account WHERE account_code = $1)",
            user_code,
        )
    assert entry_count == 0
    assert balance == Decimal("0")
