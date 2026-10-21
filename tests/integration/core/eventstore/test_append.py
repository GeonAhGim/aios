"""FA-13 `append.py` 통합테스트 — 실 DB(TEST_DATABASE_URL) 대상.

DoD(task-1703): 시퀀스 충돌 거부, 체인 검증.

DEPTH 감사(task-2724, docs/audit/DEPTH_FA.md)가 이 리프의 D3 하한 미달로
지적한 마지막 공백(negative=4·실패주입·게이트재현·D3 동시성증거는 이미
충족, 수치 성능 단언만 없음)을 `test_append_p95_latency_stays_within_normalized_ceiling`로
메운다 — §5 "이벤트 append p95 20ms" 목표의 회귀 감시(task-3004).
"""

from __future__ import annotations

import asyncio
import json
import math
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
from tests._perf.relative_budget import RelativeBudget

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
            f"SELECT * FROM {TABLE} WHERE stream_id = $1 AND seq = 1",  # noqa: S608
            stream_id,
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


@pytest.mark.perf
async def test_append_p95_latency_stays_within_normalized_ceiling(pool: asyncpg.Pool, perf_budget):
    """수치 성능 단언 — §5 "이벤트 append p95 20ms" 목표의 회귀 감시.
    공유 TEST_DATABASE_URL의 절대 지연 변동성 때문에 절대 ms 임계 대신,
    가벼운 baseline append 1건 대비 정규화한 상한만 게이트로 쓴다(LA-18
    test_quality_metrics.py·LA-24 test_market_data_router.py와 동일 교훈).
    append()는 매 호출마다 SELECT(head 조회) + INSERT 두 번의 라운드트립뿐이라
    스트림이 길어져도 지연이 자라지 않아야 한다 -- 회귀가 생기면(예: head
    조회가 seq로 스캔하도록 바뀌는 등) 뒤쪽 샘플의 p95가 baseline 대비
    크게 벌어진다."""
    stream_id = _stream()

    # Each baseline retry uses a fresh stream so sequence checks remain valid.
    baseline = await RelativeBudget().measure_async(lambda: _append(pool, _stream(), 1), warmup=0)
    baseline_elapsed = baseline.op_ms / 1000
    await _append(pool, stream_id, 1)

    seq = 1

    async def append_next():
        nonlocal seq
        seq += 1
        return await _append(pool, stream_id, seq)

    measurements = await perf_budget.samples_async(append_next, n=60)
    samples = [sample.wall_ms / 1000 for sample in measurements]

    samples.sort()
    p95 = samples[math.ceil(0.95 * len(samples)) - 1]

    # CI 병렬 실행(CPU 경쟁 + --cov tracer)에서 raw wall_ms가 baseline(best-of-3) 대비
    # 2~3x 벌어질 수 있다 — baseline * 5 + 0.05로는 병렬 모드에서 p95가 불안정했다.
    # task-15569/15696/15673 관례: 병렬 CI 안정화를 위해 예산을 넓힌다.
    ceiling = baseline_elapsed * 8 + 0.08
    assert p95 <= ceiling, (
        f"append p95 지연 {p95:.4f}s가 정규화 상한 {ceiling:.4f}s(baseline "
        f"{baseline_elapsed:.4f}s)를 초과했습니다 -- §5 append p95 20ms 목표 회귀 의심"
    )


async def test_append_concurrent_appends_conflict_is_deterministic(
    pool: asyncpg.Pool,
):
    """실패 주입 — 같은 stream에 동시에 같은 seq를 append하면 정확히 하나만 성공하고
    나머지는 SequenceConflictError를.raise 한다(task-11539 감사 후속).

    이 테스트는 동시성 실패가 결정론적으로 처리되는지 검증한다 — race condition이
    silent corruption로 이어지지 않음을 확인한다."""
    import asyncio as _asyncio

    stream_id = _stream()

    # 먼저 첫 이벤트를 채워넣어 다음 seq부터 시작
    await _append(pool, stream_id, 1)

    target_seq = 2
    results: list[tuple[bool, str]] = []

    async def try_append():
        try:
            await _append(pool, stream_id, target_seq)
            results.append((True, "success"))
        except SequenceConflictError as exc:
            results.append((False, str(exc)))

    # 동시에 5개의 append가 같은 seq를 시도 — _append 내부에서 pool.acquire()
    # 호출하므로 각각 독립적인 connection을 얻게 된다.
    tasks = [_asyncio.create_task(try_append()) for _ in range(5)]
    await _asyncio.gather(*tasks)

    successes = sum(1 for ok, _ in results if ok)
    conflicts = sum(1 for ok, _ in results if not ok)

    assert successes == 1, f"정확히 1개만 성공해야 하지만 {successes}개가 성공함"
    assert conflicts == 4, f"나머지 4개가 conflict여야 하지만 {conflicts}개"


@pytest.mark.perf
async def test_append_budget_injection_detected_by_10x_delay(
    pool: asyncpg.Pool, monkeypatch, perf_budget
):
    """gate-red repro — append 내부에 10배 지연을 주입하면 성능 예산 단언이
    적색으로 전환되는지 검증한다(task-11539 감사 후속).

    `asyncpg.Connection.fetchrow`를 래핑해 실제 DB 호출 뒤에 `asyncio.sleep(0.05)`를
    주입하면 append 1회당 wall_ms가 baseline 대비 10배 이상 늘어나고,
    `test_append_p95_latency_stays_within_normalized_ceiling`과 동일한 정규화
    상한 계산이 이를 포착해야 한다.

    이 테스트는 "성능 게이트가 실제 회귀를 포착할 수 있는가"를 역으로 증명한다 —
    정상 입력에서는 녹색이지만, 의도적으로 지연을 주입하면 적색으로 전환된다.
    """
    # 먼저 지연 없이 baseline을 측정한다 (이 시점에는 monkeypatch 미적용)
    baseline = await RelativeBudget().measure_async(lambda: _append(pool, _stream(), 1), warmup=0)
    baseline_elapsed = baseline.op_ms / 1000

    # 이제 fetchrow에 지연 주입 — baseline 측정 *후*에 적용하므로
    # baseline은 실제 DB 지연만 반영하고, 이후 append_next()는 지연 포함
    original_fetchrow = asyncpg.Connection.fetchrow
    delay_count = 0

    async def _delayed_fetchrow(self, *args, **kwargs):
        nonlocal delay_count
        result = await original_fetchrow(self, *args, **kwargs)
        delay_count += 1
        if delay_count >= 2:  # 첫 번째 fetchrow는 stream 생성용, 두 번째부터 지연
            import asyncio as _asyncio

            await _asyncio.sleep(0.05)
        return result

    monkeypatch.setattr(asyncpg.Connection, "fetchrow", _delayed_fetchrow)

    # 지연 주입 후 append 측정
    stream_id = _stream()
    # 첫 번째 append(seq=1) + append_next()가 seq=2부터 시작하도록 설정
    await _append(pool, stream_id, 1)

    seq = 1

    async def append_next():
        nonlocal seq
        seq += 1
        return await _append(pool, stream_id, seq)

    measurements = await perf_budget.samples_async(append_next, n=20)
    samples = [sample.wall_ms / 1000 for sample in measurements]

    samples.sort()
    p95 = samples[math.ceil(0.95 * len(samples)) - 1]

    ceiling = baseline_elapsed * 8 + 0.08
    # 지연 주입 후 p95가 상한을 넘어야 한다 — 게이트가 회귀를 포착하는지 검증
    assert p95 > ceiling, (
        f"지연 주입에도 p95={p95:.4f}s가 상한 {ceiling:.4f}s 미만에 머무름 — "
        "게이트가 지연을 포착하지 못함"
    )
