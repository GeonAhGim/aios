"""PostgresJournalRepository 통합테스트 — 실 DB(TEST_DATABASE_URL) 대상.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9 LC-8.
DoD(task-320): "동시 작성자 50건에서 seq가 1..50 연속·중복 0(누락되면 실패)",
"멱등키 재사용 negative" 필수.

task-5309 fix: 성공적으로 커밋되는 케이스(`test_append_raises_on_unknown_
account_and_rolls_back` 제외 — 그 테스트는 항상 예외를 던지고 아무 것도
남기지 않는다)는 `repo.append()`를 직접 부르지 않고 LC-9 `post_entry()`를
거친다. `repo.append()`를 직접 호출하면 `ledger_balance`가 갱신되지 않는데,
공유 `PLATFORM:CASH_CLEARING` 시드 계정에 매 CI 실행마다 영구히 쌓여
FA-15 `replay_verify`(저널을 처음부터 접은 값과 `ledger_balance`를 대조)를
거짓 FAIL로 만든다(esc-ci-replay_verify — `verify_integrity.py` 모듈
docstring이 같은 계정에 대해 이미 문서화한 것과 동일한 드리프트 종류,
같은 근본원인의 다른 증상). `post_entry`는 이 파일이 실제로 검증하려는
`PostgresJournalRepository.append()`(해시체인·멱등·동시성)를 그대로
내부에서 호출하므로 DoD 커버리지는 변하지 않고, 잔액만 저널과 함께
원자적으로 갱신된다(esc-2115가 `USER:*` 계정에 이미 정한 것과 같은
"공유 시드 계정에 원장 경로 밖의 쓰기를 남기지 않는다" 관례).
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from src.data.models.base import Currency
from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.ledger.adapters.postgres_balance_repository import (
    PostgresBalanceRepository,
    UnknownAccountError,
)
from src.foundation.ledger.adapters.postgres_journal_repository import PostgresJournalRepository
from src.foundation.ledger.application.post_entry import post_entry
from src.foundation.ledger.contracts.v1 import LedgerEvent, LedgerEventType, PostingLine, Side
from src.foundation.ledger.domain.idempotency import IdempotencyDigestMismatchError

_DEBIT_ACCOUNT = "PLATFORM:CASH_CLEARING"
_CREDIT_ACCOUNT = "PLATFORM:COMMISSION_REVENUE"


def _clock() -> datetime:
    return datetime.now(timezone.utc)


@pytest.fixture
def repo(pool):
    return PostgresJournalRepository(pool)


class _Ports:
    """`post_entry`가 요구하는 세 포트를 같은 `pool` 위에 묶는다(`test_post_
    entry.py`의 `_RealPorts`와 동일 관례)."""

    def __init__(self, pool) -> None:
        self.balances = PostgresBalanceRepository(pool)
        self.audit = PostgresAuditEventRepository(pool)


@pytest.fixture
def ports(pool):
    return _Ports(pool)


async def _post(conn, repo, ports, event: LedgerEvent):
    """이 파일의 성공 케이스 전용 헬퍼 — `repo.append()`가 아니라 `post_entry()`를
    거쳐 `ledger_balance`를 저널과 함께 원자적으로 갱신한다(모듈 docstring)."""
    return await post_entry(
        conn, event, journal=repo, balances=ports.balances, audit=ports.audit, clock=_clock
    )


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
        extra={"debit_account": _DEBIT_ACCOUNT, "credit_account": _CREDIT_ACCOUNT},
    )


async def test_append_persists_entry_and_links_to_prior_hash(pool, repo, ports):
    async with pool.acquire() as conn, conn.transaction():
        before = await repo.last(conn)

    event = _event()
    async with pool.acquire() as conn, conn.transaction():
        view = await _post(conn, repo, ports, event)

    assert view.replayed is False
    assert view.lines_digest
    assert view.entry_hash
    expected_prev = None if before is None else before.entry_hash
    assert view.prev_hash == expected_prev
    assert {line.account_code for line in view.lines} == {_DEBIT_ACCOUNT, _CREDIT_ACCOUNT}


async def test_append_same_event_twice_replays_without_duplicate_insert(pool, repo, ports):
    event = _event()

    async with pool.acquire() as conn, conn.transaction():
        first = await _post(conn, repo, ports, event)
    async with pool.acquire() as conn, conn.transaction():
        second = await _post(conn, repo, ports, event)

    assert second.replayed is True
    assert second.entry_id == first.entry_id
    assert second.sequence_no == first.sequence_no

    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT count(*) FROM ledger_journal_entry WHERE idempotency_key = $1",
            first.idempotency_key,
        )
    assert count == 1


async def test_append_same_key_different_content_raises_digest_mismatch(pool, repo, ports):
    """DoD: 멱등키 재사용 negative — 같은 event_ref로 다른 금액이 재전송되면
    거부해야 한다(재시도 불가 버그, 조용히 통과하면 원장이 손상된다)."""
    event_ref = f"test:{uuid4().hex}"
    event = _event(event_ref=event_ref, amount=Decimal("10.00"))

    async with pool.acquire() as conn, conn.transaction():
        await _post(conn, repo, ports, event)

    replay_event = _event(event_ref=event_ref, amount=Decimal("99.00"))
    async with pool.acquire() as conn, conn.transaction():
        with pytest.raises(IdempotencyDigestMismatchError):
            await _post(conn, repo, ports, replay_event)


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


async def test_list_since_returns_entries_in_ascending_order(pool, repo, ports):
    async with pool.acquire() as conn, conn.transaction():
        baseline = await repo.last(conn)
    baseline_seq = 0 if baseline is None else baseline.sequence_no

    posted = []
    for _ in range(3):
        async with pool.acquire() as conn, conn.transaction():
            posted.append(await _post(conn, repo, ports, _event()))

    async with pool.acquire() as conn, conn.transaction():
        since = await repo.list_since(conn, baseline_seq)

    seqs = [entry.sequence_no for entry in since]
    assert seqs == sorted(seqs)
    assert [p.sequence_no for p in posted] == seqs[-3:]


async def test_concurrent_appends_produce_contiguous_sequence_with_no_duplicates(pool, repo, ports):
    """DoD 핵심 요구: 동시 작성자 50건에서 seq가 연속·중복 0(누락되면 실패).

    전역 advisory lock(`pg_advisory_xact_lock(hashtext('ledger_journal'))`)이
    append를 직렬화하지 못하면 sequence_no가 중복되거나 UNIQUE 제약 위반으로
    일부 작성자가 예외 없이 죽는 대신 터진다 — 둘 다 이 테스트가 잡아낸다.

    이 단언(baseline+1..baseline+50 연속)은 테스트 실행 동안 이 DB에 다른 작성자가
    없다는 전제에 의존한다 — pytest-xdist로 `-n` 병렬 실행 시 워커마다 격리된 DB를
    쓰는 것은 이 테스트 파일이 아니라 `tests/conftest.py`(PLT-36,
    `ensure_worker_database`)의 책임이다.
    """
    async with pool.acquire() as conn, conn.transaction():
        before = await repo.last(conn)
    baseline_seq = 0 if before is None else before.sequence_no

    async def _write(i: int):
        event = _event(event_ref=f"concurrent:{uuid.uuid4().hex}:{i}")
        async with pool.acquire() as conn, conn.transaction():
            return await _post(conn, repo, ports, event)

    results = await asyncio.gather(*(_write(i) for i in range(50)))

    seqs = sorted(view.sequence_no for view in results)
    assert len(set(seqs)) == 50, "중복 sequence_no 발생"
    assert seqs == list(range(baseline_seq + 1, baseline_seq + 51)), "sequence_no가 연속이 아님"
