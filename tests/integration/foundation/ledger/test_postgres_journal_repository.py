"""PostgresJournalRepository 통합테스트 — 실 DB(TEST_DATABASE_URL) 대상.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9 LC-8.
DoD(task-320): "동시 작성자 50건에서 seq가 1..50 연속·중복 0(누락되면 실패)",
"멱등키 재사용 negative" 필수.
"""
from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal
from uuid import uuid4

import pytest

from src.data.models.base import Currency
from src.foundation.ledger.adapters.postgres_balance_repository import UnknownAccountError
from src.foundation.ledger.adapters.postgres_journal_repository import PostgresJournalRepository
from src.foundation.ledger.contracts.v1 import LedgerEvent, LedgerEventType, PostingLine, Side
from src.foundation.ledger.domain.idempotency import IdempotencyDigestMismatchError

_DEBIT_ACCOUNT = "PLATFORM:CASH_CLEARING"
_CREDIT_ACCOUNT = "PLATFORM:COMMISSION_REVENUE"


@pytest.fixture
def repo(pool):
    return PostgresJournalRepository(pool)


def _event(*, event_ref: str | None = None, amount: Decimal = Decimal("10.00")) -> LedgerEvent:
    return LedgerEvent(
        event_type=LedgerEventType.MANUAL_ADJUSTMENT,
        event_ref=event_ref or f"test:{uuid4().hex}",
        tenant_id=None,
        actor_subject_id=None,
        trace_id=uuid4(),
        amount=amount,
        currency=Currency.KRW,
        parties={},
        extra={},
    )


def _lines(*, amount: Decimal = Decimal("10.00")) -> list[PostingLine]:
    return [
        PostingLine(
            line_no=1, account_code=_DEBIT_ACCOUNT, side=Side.DEBIT, amount=amount,
            currency=Currency.KRW,
        ),
        PostingLine(
            line_no=2, account_code=_CREDIT_ACCOUNT, side=Side.CREDIT, amount=amount,
            currency=Currency.KRW,
        ),
    ]


async def test_append_persists_entry_and_links_to_prior_hash(pool, repo):
    async with pool.acquire() as conn, conn.transaction():
        before = await repo.last(conn)

    event = _event()
    async with pool.acquire() as conn, conn.transaction():
        view = await repo.append(conn, event, _lines())

    assert view.replayed is False
    assert view.lines_digest
    assert view.entry_hash
    expected_prev = None if before is None else before.entry_hash
    assert view.prev_hash == expected_prev
    assert {line.account_code for line in view.lines} == {_DEBIT_ACCOUNT, _CREDIT_ACCOUNT}


async def test_append_same_event_twice_replays_without_duplicate_insert(pool, repo):
    event = _event()
    lines = _lines()

    async with pool.acquire() as conn, conn.transaction():
        first = await repo.append(conn, event, lines)
    async with pool.acquire() as conn, conn.transaction():
        second = await repo.append(conn, event, lines)

    assert second.replayed is True
    assert second.entry_id == first.entry_id
    assert second.sequence_no == first.sequence_no

    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT count(*) FROM ledger_journal_entry WHERE idempotency_key = $1",
            first.idempotency_key,
        )
    assert count == 1


async def test_append_same_key_different_content_raises_digest_mismatch(pool, repo):
    """DoD: 멱등키 재사용 negative — 같은 event_ref로 다른 금액이 재전송되면
    거부해야 한다(재시도 불가 버그, 조용히 통과하면 원장이 손상된다)."""
    event_ref = f"test:{uuid4().hex}"
    event = _event(event_ref=event_ref, amount=Decimal("10.00"))

    async with pool.acquire() as conn, conn.transaction():
        await repo.append(conn, event, _lines(amount=Decimal("10.00")))

    replay_event = _event(event_ref=event_ref, amount=Decimal("99.00"))
    async with pool.acquire() as conn, conn.transaction():
        with pytest.raises(IdempotencyDigestMismatchError):
            await repo.append(conn, replay_event, _lines(amount=Decimal("99.00")))


async def test_append_raises_on_unknown_account_and_rolls_back(pool, repo):
    event = _event()
    bad_lines = [
        PostingLine(
            line_no=1, account_code="PLATFORM:DOES_NOT_EXIST", side=Side.DEBIT,
            amount=Decimal("5.00"), currency=Currency.KRW,
        ),
        PostingLine(
            line_no=2, account_code=_CREDIT_ACCOUNT, side=Side.CREDIT,
            amount=Decimal("5.00"), currency=Currency.KRW,
        ),
    ]

    with pytest.raises(UnknownAccountError):
        async with pool.acquire() as conn, conn.transaction():
            await repo.append(conn, event, bad_lines)

    async with pool.acquire() as conn:
        found = await conn.fetchval(
            "SELECT 1 FROM ledger_journal_entry WHERE idempotency_key = $1",
            f"{event.event_type.value}:{event.event_ref}",
        )
    assert found is None


async def test_list_since_returns_entries_in_ascending_order(pool, repo):
    async with pool.acquire() as conn, conn.transaction():
        baseline = await repo.last(conn)
    baseline_seq = 0 if baseline is None else baseline.sequence_no

    posted = []
    for _ in range(3):
        async with pool.acquire() as conn, conn.transaction():
            posted.append(await repo.append(conn, _event(), _lines()))

    async with pool.acquire() as conn, conn.transaction():
        since = await repo.list_since(conn, baseline_seq)

    seqs = [entry.sequence_no for entry in since]
    assert seqs == sorted(seqs)
    assert [p.sequence_no for p in posted] == seqs[-3:]


async def test_concurrent_appends_produce_contiguous_sequence_with_no_duplicates(pool, repo):
    """DoD 핵심 요구: 동시 작성자 50건에서 seq가 연속·중복 0(누락되면 실패).

    전역 advisory lock(`pg_advisory_xact_lock(hashtext('ledger_journal'))`)이
    append를 직렬화하지 못하면 sequence_no가 중복되거나 UNIQUE 제약 위반으로
    일부 작성자가 예외 없이 죽는 대신 터진다 — 둘 다 이 테스트가 잡아낸다.
    """
    async with pool.acquire() as conn, conn.transaction():
        before = await repo.last(conn)
    baseline_seq = 0 if before is None else before.sequence_no

    async def _write(i: int):
        event = _event(event_ref=f"concurrent:{uuid.uuid4().hex}:{i}")
        async with pool.acquire() as conn, conn.transaction():
            return await repo.append(conn, event, _lines())

    results = await asyncio.gather(*(_write(i) for i in range(50)))

    seqs = sorted(view.sequence_no for view in results)
    assert len(set(seqs)) == 50, "중복 sequence_no 발생"
    assert seqs == list(range(baseline_seq + 1, baseline_seq + 51)), "sequence_no가 연속이 아님"
