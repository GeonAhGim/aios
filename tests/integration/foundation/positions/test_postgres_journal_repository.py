"""PostgresJournalRepository 통합테스트 — 실 DB(TEST_DATABASE_URL) 대상.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9 LB-9.
DoD(task-375): "동일 position_key에 동시 20건 append 시 sequence_no가
1..20 연속·중복 0·해시체인 무결", "idempotency_key 중복 재삽입은 새 행을
만들지 않음"(negative).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from src.data.models.base import Currency, Money
from src.foundation.positions.adapters.postgres_journal_repository import (
    IdempotencyDigestMismatchError,
    PostgresJournalRepository,
    UnknownPositionError,
)
from src.foundation.positions.contracts.v1 import JournalEntryType
from src.foundation.positions.domain.position_key import PositionKey
from tests.integration.conftest import create_test_user
from tests.integration.foundation.positions.conftest import create_pos_account, open_position


def _key() -> str:
    return str(
        PositionKey(
            venue="TESTVENUE",
            instrument_id=f"INST{uuid4().hex[:8]}",
            strategy_id="default",
            execution_id="paper",
        )
    )


_OCCURRED_AT = datetime(2026, 1, 1, tzinfo=timezone.utc)


@pytest.fixture
def repo(pool):
    return PostgresJournalRepository(pool)


async def _open(pool):
    tenant_id = await create_test_user(pool)
    account_id = await create_pos_account(pool, tenant_id)
    position_key = _key()
    await open_position(pool, tenant_id=tenant_id, account_id=account_id, position_key=position_key)
    return tenant_id, account_id, position_key


async def _append(
    repo, pool, position_key, *, idempotency_key: str | None = None, qty=Decimal("1")
):
    async with pool.acquire() as conn, conn.transaction():
        return await repo.append(
            conn,
            position_key=position_key,
            entry_type=JournalEntryType.FILL,
            qty_delta=qty,
            price=Money(amount=Decimal("100"), currency=Currency.KRW),
            fee=Money(amount=Decimal("1"), currency=Currency.KRW),
            realized_pnl_base=Decimal("0"),
            fx_rate=None,
            fx_source=None,
            source_event_type="fill",
            source_event_id=uuid4().hex,
            idempotency_key=idempotency_key or f"fill:{uuid4().hex}",
            occurred_at=_OCCURRED_AT,
        )


async def test_append_persists_first_entry_with_no_prev_hash(pool, repo):
    _, _, position_key = await _open(pool)

    view = await _append(repo, pool, position_key)

    assert view.sequence_no == 1
    assert view.prev_hash is None
    assert view.entry_hash


async def test_append_links_second_entry_to_first_hash(pool, repo):
    _, _, position_key = await _open(pool)

    first = await _append(repo, pool, position_key)
    second = await _append(repo, pool, position_key)

    assert second.sequence_no == 2
    assert second.prev_hash == first.entry_hash


async def test_append_same_idempotency_key_replays_without_duplicate_insert(pool, repo):
    _, _, position_key = await _open(pool)
    key = f"fill:{uuid4().hex}"

    first = await _append(repo, pool, position_key, idempotency_key=key)
    second = await _append(repo, pool, position_key, idempotency_key=key)

    assert second.id == first.id
    assert second.sequence_no == first.sequence_no

    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT count(*) FROM pos_journal WHERE idempotency_key = $1", key
        )
    assert count == 1


async def test_append_same_key_different_content_raises_digest_mismatch(pool, repo):
    _, _, position_key = await _open(pool)
    key = f"fill:{uuid4().hex}"

    await _append(repo, pool, position_key, idempotency_key=key, qty=Decimal("1"))

    with pytest.raises(IdempotencyDigestMismatchError):
        await _append(repo, pool, position_key, idempotency_key=key, qty=Decimal("2"))


async def test_append_without_snapshot_raises_unknown_position(pool, repo):
    position_key = _key()

    with pytest.raises(UnknownPositionError):
        await _append(repo, pool, position_key)


async def test_list_for_returns_entries_in_ascending_order(pool, repo):
    _, _, position_key = await _open(pool)
    posted = [await _append(repo, pool, position_key) for _ in range(3)]

    async with pool.acquire() as conn, conn.transaction():
        since = await repo.list_for(conn, position_key)

    assert [e.sequence_no for e in since] == [1, 2, 3]
    assert [p.id for p in posted] == [e.id for e in since]


async def test_last_returns_none_when_empty_and_latest_otherwise(pool, repo):
    _, _, position_key = await _open(pool)

    async with pool.acquire() as conn, conn.transaction():
        assert await repo.last(conn, position_key) is None

    latest = await _append(repo, pool, position_key)
    await _append(repo, pool, position_key)
    third = await _append(repo, pool, position_key)

    async with pool.acquire() as conn, conn.transaction():
        result = await repo.last(conn, position_key)
    assert result is not None
    assert result.sequence_no == third.sequence_no
    assert result.sequence_no != latest.sequence_no


async def test_concurrent_appends_produce_contiguous_hash_chained_sequence(pool, repo):
    """DoD 핵심 요구: 동일 position_key에 동시 20건 append 시 sequence_no가
    1..20 연속·중복 0이고 해시체인이 무결해야 한다.

    `pg_advisory_xact_lock(hashtext('pos_journal'), hashtext(position_key))`가
    position_key 단위 append를 직렬화하지 못하면 sequence_no 중복이나
    (position_key, sequence_no) UNIQUE 위반이 발생한다.
    """
    _, _, position_key = await _open(pool)

    async def _write(i: int):
        return await _append(repo, pool, position_key, idempotency_key=f"fill:{uuid4().hex}:{i}")

    results = await asyncio.gather(*(_write(i) for i in range(20)))

    seqs = sorted(view.sequence_no for view in results)
    assert seqs == list(range(1, 21)), "sequence_no가 1..20 연속이 아니거나 중복이 있습니다"

    async with pool.acquire() as conn, conn.transaction():
        chain = await repo.list_for(conn, position_key)

    expected_prev = None
    for entry in chain:
        assert entry.prev_hash == expected_prev, f"seq={entry.sequence_no} 해시체인 단절"
        expected_prev = entry.entry_hash
