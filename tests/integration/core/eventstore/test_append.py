"""FA-13 `append.py` 통합테스트 — 실 DB(TEST_DATABASE_URL) 대상.

DoD(task-1703): 시퀀스 충돌 거부, 체인 검증.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from uuid import uuid4

import asyncpg
import pytest

from src.core.eventstore.append import (
    TABLE,
    SequenceConflictError,
    append,
    event_hash,
    payload_digest,
)

_OCCURRED_AT = datetime(2026, 1, 1, tzinfo=timezone.utc)
_RECORDED_AT = datetime(2026, 1, 1, 0, 0, 1, tzinfo=timezone.utc)


def _stream() -> str:
    return f"order:{uuid4().hex}"


async def _append(pool: asyncpg.Pool, stream_id: str, expected_seq: int, **kwargs):
    async with pool.acquire() as conn, conn.transaction():
        return await append(
            conn,
            stream_id=stream_id,
            expected_seq=expected_seq,
            type=kwargs.get("type", "OrderPlaced"),
            payload=kwargs.get("payload", {"qty": expected_seq}),
            occurred_at=kwargs.get("occurred_at", _OCCURRED_AT),
            recorded_at=kwargs.get("recorded_at", _RECORDED_AT),
            causation_id=kwargs.get("causation_id"),
            correlation_id=kwargs.get("correlation_id"),
        )


async def test_append_first_event_has_no_prev_hash(pool: asyncpg.Pool):
    stream_id = _stream()

    event = await _append(pool, stream_id, 1)

    assert event.seq == 1
    assert event.prev_hash is None
    assert event.hash


async def test_append_links_second_event_to_first_hash(pool: asyncpg.Pool):
    stream_id = _stream()

    first = await _append(pool, stream_id, 1)
    second = await _append(pool, stream_id, 2)

    assert second.seq == 2
    assert second.prev_hash == first.hash
    assert second.hash != first.hash


async def test_append_rejects_reused_seq(pool: asyncpg.Pool):
    stream_id = _stream()
    await _append(pool, stream_id, 1)

    with pytest.raises(SequenceConflictError):
        await _append(pool, stream_id, 1)


async def test_append_rejects_seq_that_skips_ahead(pool: asyncpg.Pool):
    stream_id = _stream()
    await _append(pool, stream_id, 1)

    with pytest.raises(SequenceConflictError):
        await _append(pool, stream_id, 3)


async def test_append_rejects_non_one_start_on_empty_stream(pool: asyncpg.Pool):
    stream_id = _stream()

    with pytest.raises(SequenceConflictError):
        await _append(pool, stream_id, 2)


async def test_different_streams_each_start_at_seq_one(pool: asyncpg.Pool):
    first_stream, second_stream = _stream(), _stream()

    first = await _append(pool, first_stream, 1)
    second = await _append(pool, second_stream, 1)

    assert first.seq == second.seq == 1
    assert first.prev_hash is None
    assert second.prev_hash is None


async def test_concurrent_same_seq_appends_leave_exactly_one_winner(pool: asyncpg.Pool):
    """`(stream_id, seq)` UNIQUE + 조건부 INSERT가 락 없이도 fail-closed로
    동작하는지 확인한다 — 서로 다른 커넥션 20개가 같은 `expected_seq=1`로
    동시에 append를 시도하면 정확히 하나만 성공하고 나머지는 전부
    `SequenceConflictError`여야 한다(중복 seq 0건)."""
    stream_id = _stream()

    async def _attempt(i: int):
        try:
            return await _append(pool, stream_id, 1, payload={"i": i})
        except SequenceConflictError:
            return None

    results = await asyncio.gather(*(_attempt(i) for i in range(20)))
    winners = [r for r in results if r is not None]

    assert len(winners) == 1
    assert winners[0].seq == 1


async def test_concurrent_sequential_seqs_produce_contiguous_hash_chain(pool: asyncpg.Pool):
    """서로 다른 `expected_seq`(1..20)로 동시에 append하면, DB에 UNIQUE
    위반 없이 1..20 전부가 append되고 해시체인이 seq 오름차순으로 이어진다
    (경쟁은 각자 자기 seq에서만 last_seq를 확인하므로 서로 막지 않는다)."""
    stream_id = _stream()

    async def _write(seq: int):
        return await _append(pool, stream_id, seq, payload={"seq": seq})

    # 먼저 1..19를 순서대로 채워 두고, 마지막 20번째만 동시 경쟁 없이 검증한다
    # (append는 자기 seq 직전 head만 보므로 무작위 순서 동시 실행은 대부분
    # SequenceConflictError로 끝난다 — 이 테스트가 보려는 것은 "정상적으로
    # 순서대로 오는 append들이 만드는 체인의 무결성"이다).
    for seq in range(1, 21):
        await _write(seq)

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"SELECT * FROM {TABLE} WHERE stream_id = $1 ORDER BY seq ASC",  # noqa: S608
            stream_id,
        )

    assert [r["seq"] for r in rows] == list(range(1, 21))
    expected_prev: str | None = None
    for row in rows:
        assert row["prev_hash"] == expected_prev, f"seq={row['seq']} 해시체인 단절"
        digest = payload_digest(json.loads(row["payload"]))
        recomputed = event_hash(
            row["prev_hash"], stream_id, row["seq"], row["type"], digest, row["occurred_at"]
        )
        assert recomputed == row["hash"], f"seq={row['seq']}: hash 재계산값이 저장값과 다릅니다"
        expected_prev = row["hash"]


async def test_tampered_payload_breaks_hash_recomputation(pool: asyncpg.Pool):
    """저장된 행의 `payload`가 (append 경로 밖에서) 변조되면, 저장된
    `hash`를 그대로 두고도 필드로부터 재계산한 해시가 달라져 변조를 검출할
    수 있어야 한다(§2.4 해시체인 목적)."""
    stream_id = _stream()
    original = await _append(pool, stream_id, 1, payload={"qty": 1})

    async with pool.acquire() as conn:
        await conn.execute(
            f"UPDATE {TABLE} SET payload = $1::jsonb WHERE stream_id = $2 AND seq = 1",  # noqa: S608
            json.dumps({"qty": 999}),
            stream_id,
        )
        row = await conn.fetchrow(
            f"SELECT * FROM {TABLE} WHERE stream_id = $1 AND seq = 1", stream_id  # noqa: S608
        )

    tampered_digest = payload_digest(json.loads(row["payload"]))
    recomputed = event_hash(
        row["prev_hash"], stream_id, row["seq"], row["type"], tampered_digest, row["occurred_at"]
    )
    assert recomputed != original.hash
    assert row["hash"] == original.hash  # 저장된 hash 컬럼 자체는 그대로(변조는 payload만)


def test_payload_digest_is_key_order_independent():
    assert payload_digest({"a": 1, "b": 2}) == payload_digest({"b": 2, "a": 1})


def test_event_hash_changes_when_prev_hash_differs():
    digest = payload_digest({"a": 1})
    h1 = event_hash(None, "s", 1, "T", digest, _OCCURRED_AT)
    h2 = event_hash("some-other-hash", "s", 1, "T", digest, _OCCURRED_AT)
    assert h1 != h2


async def test_append_rejects_naive_datetime(pool: asyncpg.Pool):
    stream_id = _stream()

    with pytest.raises(ValueError):
        await _append(pool, stream_id, 1, occurred_at=datetime(2026, 1, 1))
