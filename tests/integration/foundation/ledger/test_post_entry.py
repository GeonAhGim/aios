"""LC-9 `post_entry` 통합테스트 — 실 DB(TEST_DATABASE_URL) 대상.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§6, §9 LC-9.
DoD(task-330): "감사 실패 주입 시 분개·라인·잔액 전부 롤백", "write_frozen=true면
거부", "REPLAY 무중복", "DIGEST_MISMATCH 거부+DENIED 감사" 필수.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from src.data.models.base import Currency
from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.ledger.adapters.postgres_balance_repository import PostgresBalanceRepository
from src.foundation.ledger.adapters.postgres_journal_repository import PostgresJournalRepository
from src.foundation.ledger.application.post_entry import LedgerWriteFrozenError, post_entry
from src.foundation.ledger.contracts.v1 import AccountType, LedgerEvent, LedgerEventType, UserSub
from src.foundation.ledger.domain.chart_of_accounts import user_account
from src.foundation.ledger.domain.idempotency import IdempotencyDigestMismatchError

_PLATFORM_CASH_CLEARING = "PLATFORM:CASH_CLEARING"


def _clock() -> datetime:
    return datetime.now(timezone.utc)


async def _create_user_available_account(
    pool, user_id: UUID, *, initial_balance: Decimal = Decimal("0")
) -> str:
    """`USER:{user_id}:AVAILABLE` 계정을 만든다 — 시드 계정이 아니라 테스트마다
    새 `user_id`로 격리한다(공유 PLATFORM 계정과 달리 잔액 오염 걱정이 없다)."""
    code = user_account(user_id, UserSub.AVAILABLE)
    async with pool.acquire() as conn:
        account_id = await conn.fetchval(
            "INSERT INTO ledger_account (account_code, account_type, currency, allow_negative) "
            "VALUES ($1, $2, $3, FALSE) RETURNING account_id",
            code,
            AccountType.LIABILITY.value,
            Currency.KRW.value,
        )
        await conn.execute(
            "INSERT INTO ledger_balance (account_id, balance, allow_negative, last_entry_seq) "
            "VALUES ($1, $2, FALSE, 0)",
            account_id,
            initial_balance,
        )
    return code


def _topup_event(
    *, event_ref: str, user_id: UUID, amount: Decimal = Decimal("10.00")
) -> LedgerEvent:
    return LedgerEvent(
        event_type=LedgerEventType.TOPUP_CONFIRMED,
        event_ref=event_ref,
        tenant_id=None,
        actor_subject_id=None,
        trace_id=uuid4(),
        amount=amount,
        currency=Currency.KRW,
        parties={"user": user_id},
        extra={},
    )


class _RealPorts:
    def __init__(self, pool):
        self.journal = PostgresJournalRepository(pool)
        self.balances = PostgresBalanceRepository(pool)
        self.audit = PostgresAuditEventRepository(pool)


class _BoomAuditAppender:
    async def append_event_in(self, conn, **kwargs):
        raise RuntimeError("injected audit failure")


class _NoPrecheckJournal:
    """`find_by_idempotency_key` 사전체크가 항상 "없음"이라고 답하는 래퍼 —
    동시 재시도 두 건이 락 없는 사전체크를 모두 통과한 뒤 `append`(advisory
    lock)에서 처음 직렬화되는 레이스 창을, 순차 호출만으로 재현한다(LC-9
    회귀). `append` 자체는 실제 저장소에 그대로 위임하므로 두 번째 호출은
    `append` 내부 판정에 의해 `replayed=True`를 받는다."""

    def __init__(self, real: PostgresJournalRepository) -> None:
        self._real = real

    async def append(self, conn, entry, lines):
        return await self._real.append(conn, entry, lines)

    async def find_by_idempotency_key(self, conn, key):
        return None

    async def list_since(self, conn, seq):
        return await self._real.list_since(conn, seq)

    async def last(self, conn):
        return await self._real.last(conn)


@pytest.fixture
def ports(pool):
    return _RealPorts(pool)


async def test_post_entry_persists_journal_and_updates_balances(pool, ports):
    user_id = uuid4()
    user_code = await _create_user_available_account(pool, user_id)
    event = _topup_event(event_ref=f"topup:{uuid4().hex}", user_id=user_id, amount=Decimal("10.00"))

    async with pool.acquire() as conn, conn.transaction():
        view = await post_entry(
            conn, event, journal=ports.journal, balances=ports.balances,
            audit=ports.audit, clock=_clock,
        )

    assert view.replayed is False
    assert {line.account_code for line in view.lines} == {_PLATFORM_CASH_CLEARING, user_code}

    async with pool.acquire() as conn, conn.transaction():
        balances = await ports.balances.get_for_update(conn, [user_code])
    assert balances[user_code].balance == Decimal("10.00")


async def test_post_entry_replays_without_duplicate_journal_or_audit(pool, ports):
    user_id = uuid4()
    user_code = await _create_user_available_account(pool, user_id)
    event_ref = f"topup:{uuid4().hex}"
    event = _topup_event(event_ref=event_ref, user_id=user_id, amount=Decimal("10.00"))

    async with pool.acquire() as conn, conn.transaction():
        first = await post_entry(
            conn, event, journal=ports.journal, balances=ports.balances,
            audit=ports.audit, clock=_clock,
        )
    async with pool.acquire() as conn, conn.transaction():
        second = await post_entry(
            conn, event, journal=ports.journal, balances=ports.balances,
            audit=ports.audit, clock=_clock,
        )

    assert second.replayed is True
    assert second.entry_id == first.entry_id

    async with pool.acquire() as conn:
        entry_count = await conn.fetchval(
            "SELECT count(*) FROM ledger_journal_entry WHERE idempotency_key = $1",
            first.idempotency_key,
        )
        audit_count = await conn.fetchval(
            "SELECT count(*) FROM foundation_audit_event WHERE aggregate_id = $1",
            first.entry_id,
        )
        balance = await conn.fetchval(
            "SELECT lb.balance FROM ledger_balance lb JOIN ledger_account la "
            "ON la.account_id = lb.account_id WHERE la.account_code = $1",
            user_code,
        )
    assert entry_count == 1
    # 2 = journal.append의 FK 링크용 감사(LC-8b) + post_entry 자신의 커맨드 단위
    # 감사(LC-9) — 둘 다 첫 호출에서만 생기고, replay는 어느 쪽도 추가하지 않는다.
    assert audit_count == 2
    assert balance == Decimal("10.00")


async def test_post_entry_race_after_precheck_applies_balance_once(pool, ports):
    """LC-9 결함 수정 회귀: 사전체크(`find_by_idempotency_key`)를 우회해 두 요청
    모두 `append`에 진입시킨다. 잔액 적용·SUCCESS 감사 스킵 여부의 유일한
    근거는 `journal.append` 반환값의 `replayed`여야 한다 — 그렇지 않으면
    (수정 전처럼) 두 번째 요청도 `balances.apply`를 실행해 잔액이 두 번
    적용된다."""
    user_id = uuid4()
    user_code = await _create_user_available_account(pool, user_id)
    event_ref = f"topup:{uuid4().hex}"
    event = _topup_event(event_ref=event_ref, user_id=user_id, amount=Decimal("10.00"))
    racy_journal = _NoPrecheckJournal(ports.journal)

    async with pool.acquire() as conn, conn.transaction():
        first = await post_entry(
            conn, event, journal=racy_journal, balances=ports.balances,
            audit=ports.audit, clock=_clock,
        )
    async with pool.acquire() as conn, conn.transaction():
        second = await post_entry(
            conn, event, journal=racy_journal, balances=ports.balances,
            audit=ports.audit, clock=_clock,
        )

    assert first.replayed is False
    assert second.replayed is True
    assert second.entry_id == first.entry_id

    async with pool.acquire() as conn:
        entry_count = await conn.fetchval(
            "SELECT count(*) FROM ledger_journal_entry WHERE idempotency_key = $1",
            first.idempotency_key,
        )
        line_count = await conn.fetchval(
            "SELECT count(*) FROM ledger_posting_line WHERE entry_id = $1",
            first.entry_id,
        )
        audit_count = await conn.fetchval(
            "SELECT count(*) FROM foundation_audit_event WHERE aggregate_id = $1",
            first.entry_id,
        )
        balance = await conn.fetchval(
            "SELECT lb.balance FROM ledger_balance lb JOIN ledger_account la "
            "ON la.account_id = lb.account_id WHERE la.account_code = $1",
            user_code,
        )
    assert entry_count == 1
    assert line_count == len(first.lines)
    # 1 = journal.append의 FK 링크용 감사(LC-8b) + 1 = post_entry 자신의
    # 커맨드 단위 SUCCESS 감사(LC-9, 첫 호출에서만) — replay된 두 번째
    # 호출은 어느 쪽도 추가하지 않는다.
    assert audit_count == 2
    assert balance == Decimal("10.00")


async def test_post_entry_digest_mismatch_denies_and_emits_denied_audit(pool, ports):
    user_id = uuid4()
    await _create_user_available_account(pool, user_id)
    event_ref = f"topup:{uuid4().hex}"
    first_event = _topup_event(event_ref=event_ref, user_id=user_id, amount=Decimal("10.00"))

    async with pool.acquire() as conn, conn.transaction():
        first = await post_entry(
            conn, first_event, journal=ports.journal, balances=ports.balances,
            audit=ports.audit, clock=_clock,
        )

    # DENIED 감사 이벤트는 post_entry가 여는 게 아니라 호출자의 트랜잭션과
    # 함께 커밋된다 — 예외를 트랜잭션 경계 *안에서* 잡아야 살아남는다(모듈
    # docstring 참고). 경계 밖에서 raises하면 그 커밋 전체가 롤백돼 DENIED
    # 행도 함께 사라진다.
    mismatched_event = _topup_event(event_ref=event_ref, user_id=user_id, amount=Decimal("99.00"))
    async with pool.acquire() as conn, conn.transaction():
        with pytest.raises(IdempotencyDigestMismatchError):
            await post_entry(
                conn, mismatched_event, journal=ports.journal, balances=ports.balances,
                audit=ports.audit, clock=_clock,
            )

    async with pool.acquire() as conn:
        outcome = await conn.fetchval(
            "SELECT outcome FROM foundation_audit_event "
            "WHERE aggregate_id = $1 AND outcome = 'DENIED'",
            first.entry_id,
        )
    assert outcome == "DENIED"


async def test_post_entry_rejects_when_ledger_frozen(pool, ports):
    user_id = uuid4()
    await _create_user_available_account(pool, user_id)
    event = _topup_event(event_ref=f"topup:{uuid4().hex}", user_id=user_id)

    async with pool.acquire() as conn:
        await conn.execute("UPDATE ledger_control SET write_frozen = TRUE WHERE id = 1")
    try:
        with pytest.raises(LedgerWriteFrozenError):
            async with pool.acquire() as conn, conn.transaction():
                await post_entry(
                    conn, event, journal=ports.journal, balances=ports.balances,
                    audit=ports.audit, clock=_clock,
                )
    finally:
        async with pool.acquire() as conn:
            await conn.execute("UPDATE ledger_control SET write_frozen = FALSE WHERE id = 1")

    async with pool.acquire() as conn:
        found = await conn.fetchval(
            "SELECT 1 FROM ledger_journal_entry WHERE idempotency_key = $1",
            f"{event.event_type.value}:{event.event_ref}",
        )
    assert found is None


async def test_post_entry_audit_failure_rolls_back_journal_lines_and_balance(pool, ports):
    user_id = uuid4()
    user_code = await _create_user_available_account(pool, user_id)
    event = _topup_event(event_ref=f"topup:{uuid4().hex}", user_id=user_id, amount=Decimal("10.00"))

    with pytest.raises(RuntimeError):
        async with pool.acquire() as conn, conn.transaction():
            await post_entry(
                conn, event, journal=ports.journal, balances=ports.balances,
                audit=_BoomAuditAppender(), clock=_clock,
            )

    async with pool.acquire() as conn:
        entry_found = await conn.fetchval(
            "SELECT 1 FROM ledger_journal_entry WHERE idempotency_key = $1",
            f"{event.event_type.value}:{event.event_ref}",
        )
        balance = await conn.fetchval(
            "SELECT lb.balance FROM ledger_balance lb JOIN ledger_account la "
            "ON la.account_id = lb.account_id WHERE la.account_code = $1",
            user_code,
        )
    assert entry_found is None
    assert balance == Decimal("0")
