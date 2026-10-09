"""FA-14 통합테스트 — 실DB(TEST_DATABASE_URL)에서 `ledger_journal_entry`+
`ledger_posting_line` 전건을 재생한 `ledger` 투영이 현재 `ledger_balance`와
필드 단위로 같은지 검증한다.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#§9 FA-14 DoD.
DoD(1) 필드 단위 동일(불일치 1건이면 FAIL). DoD(2) 배선증명 negative — 원천
이벤트 1건을 고의로 빼면 대조가 실제로 FAIL함을 같은 테스트에서 단언한다
(항상 통과하는 대조는 반려).

`orders`·`positions` 투영 검증은 `test_projections.py`·
`test_projections_positions.py`로 분리돼 있다(책임 단위 분할,
CLAUDE.md ADR-2026-09-10-C §7).
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from src.data.models.base import Currency
from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.ledger.adapters.postgres_balance_repository import PostgresBalanceRepository
from src.foundation.ledger.adapters.postgres_journal_repository import PostgresJournalRepository
from src.foundation.ledger.application.post_entry import post_entry
from src.foundation.ledger.contracts.v1 import AccountType, LedgerEvent, LedgerEventType, UserSub
from src.foundation.ledger.domain import eventstore_projection as ledger_projection
from src.foundation.ledger.domain.chart_of_accounts import user_account


def _clock() -> datetime:
    return datetime.now(timezone.utc)


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
            code,
            kind.value,
            Currency.KRW.value,
            allow_negative,
        )
        await conn.execute(
            "INSERT INTO ledger_balance (account_id, allow_negative, last_entry_seq) "
            "VALUES ($1, $2, 0)",
            account_id,
            allow_negative,
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
                conn,
                event,
                journal=journal,
                balances=balances,
                audit=audit,
                clock=_clock,
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
