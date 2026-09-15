"""LA-21 적대적 — tenant A의 배치 조회를 B가 시도 → 존재 자체 비노출(404 동형).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§8.3 LA-21
("tenant A의 배치 조회를 B가 시도 → 존재 자체 비노출(404 동형)").

task-655가 실증한 실결함(`BatchRepository.get(conn, batch_id)`에
`tenant_id` 인자 자체가 없어 아무 tenant나 배치를 조회할 수 있었던 문제)은
**LA-22(task-825)**가 고쳤다 — `get()`에 `tenant_id` 파라미터를 추가하고
`WHERE tenant_id IS NOT DISTINCT FROM $2`로 필터한다
(`ports/batch_repository.py`, `adapters/postgres_batch_repository.py`).
이 테스트는 attacker가 자신의 `tenant_id`로 owner의 `batch_id`를 조회할 때
"행이 없음"과 동형으로 `None`이 반환됨을(§8.3 "404 동형") 검증한다.

DEEPEN(task-2985, docs/audit/DEPTH_LA_LB_LC.md) — 원 task-825가 D3 축
하한 미달(D1)로 판정됐다: negative가 1건뿐이었고, failure-injection·수치
성능 단언·이 리프 파일 내 게이트 적색 재현·적대적 replay/동시성 증빙이
전부 없었다. 아래에 6건을 추가해 채운다(negative 3건 총계, failure-
injection 1건, 성능 단언 1건, 게이트 적색 재현 1건, replay/동시성 2건).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import asyncpg
import pytest

from src.foundation.market_data.adapters.postgres_batch_repository import (
    PostgresBatchRepository,
    _reconstruct_verdict,
    _stored_range,
)
from src.foundation.market_data.adapters.postgres_candle_store import PostgresCandleStore
from src.foundation.market_data.contracts.v1 import (
    CandleRecord,
    IngestBatchResult,
    QualityVerdict,
    SeriesKey,
    TickIngestBatchResult,
    Timeframe,
    Venue,
    Verdict,
)
from tests.integration.conftest import create_test_tenant


@pytest.fixture
def candle_store(pool):
    return PostgresCandleStore(pool)


@pytest.fixture
def batch_repo(pool):
    return PostgresBatchRepository(pool)


async def _audit_event_id(conn: asyncpg.Connection) -> uuid.UUID:
    return await conn.fetchval(
        "INSERT INTO foundation_audit_event "
        "(sequence_no, aggregate_type, aggregate_id, action, outcome, trace_id, "
        " payload_hash, payload, event_hash) "
        "VALUES ($1, 'test.market_data', gen_random_uuid(), 'test.md.ingest', 'SUCCESS', "
        " gen_random_uuid(), 'deadbeef', '{}'::jsonb, 'deadbeef') RETURNING id",
        uuid.uuid4().int % (2**62),
    )


async def _instrument_id(conn: asyncpg.Connection) -> uuid.UUID:
    symbol = f"TEST-{uuid.uuid4().hex}"
    return await conn.fetchval(
        "INSERT INTO md_instrument "
        "(venue, canonical_symbol, venue_symbol, asset_class, tick_size, lot_size, "
        " status, listed_at) "
        "VALUES ('BITGET', $1, $1, 'CRYPTO', 0.01, 0.0001, 'LISTED', now()) "
        "RETURNING instrument_id",
        symbol,
    )


async def _seed_owner_batch(
    conn: asyncpg.Connection, batch_repo, candle_store, *, tenant_id, instrument_id
) -> IngestBatchResult:
    audit_event_id = await _audit_event_id(conn)
    key = SeriesKey(venue=Venue.BITGET, instrument_id=instrument_id, timeframe=Timeframe.M1)
    t0 = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    candle = CandleRecord(
        key=key,
        open_time=t0,
        close_time=t0 + timedelta(minutes=1),
        open=Decimal("100"),
        high=Decimal("110"),
        low=Decimal("90"),
        close=Decimal("105"),
        volume=Decimal("10"),
    )
    batch = IngestBatchResult(
        batch_id=uuid.uuid4(),
        tenant_id=tenant_id,
        source="test",
        venue=Venue.BITGET,
        instrument_id=instrument_id,
        timeframe=Timeframe.M1,
        range_start=t0,
        range_end=t0 + timedelta(minutes=1),
        request_fingerprint=f"fp-{uuid.uuid4().hex}",
        verdict=QualityVerdict(
            verdict=Verdict.ACCEPT, accepted=1, quarantined=0, rejected=0, issues=[]
        ),
        batch_hash=f"hash-{uuid.uuid4().hex}",
        audit_event_id=audit_event_id,
        stored_range=None,
    )
    await batch_repo.create(conn, batch)
    await candle_store.upsert_batch(conn, batch.batch_id, [candle])
    return batch


async def _seed_owner_tick_batch(
    conn: asyncpg.Connection, batch_repo, *, tenant_id, instrument_id
) -> TickIngestBatchResult:
    audit_event_id = await _audit_event_id(conn)
    t0 = datetime.now(timezone.utc).replace(microsecond=0)
    batch = TickIngestBatchResult(
        batch_id=uuid.uuid4(),
        tenant_id=tenant_id,
        source="test",
        venue=Venue.BITGET,
        instrument_id=instrument_id,
        range_start=t0,
        range_end=t0 + timedelta(minutes=1),
        request_fingerprint=f"fp-{uuid.uuid4().hex}",
        verdict=QualityVerdict(
            verdict=Verdict.ACCEPT, accepted=1, quarantined=0, rejected=0, issues=[]
        ),
        batch_hash=f"hash-{uuid.uuid4().hex}",
        audit_event_id=audit_event_id,
    )
    await batch_repo.create_tick_batch(conn, batch)
    return batch


def _query_logger(sink: list[str]):
    def _log(record: object) -> None:
        sink.append(getattr(record, "query", ""))

    return _log


async def test_cross_tenant_batch_get_does_not_leak_existence(pool, batch_repo, candle_store):
    owner_id = await create_test_tenant(pool)
    attacker_id = await create_test_tenant(pool)
    assert attacker_id != owner_id

    async with pool.acquire() as conn, conn.transaction():
        instrument_id = await _instrument_id(conn)
        batch = await _seed_owner_batch(
            conn, batch_repo, candle_store, tenant_id=owner_id, instrument_id=instrument_id
        )

    async with pool.acquire() as conn:
        owner_view = await batch_repo.get(conn, batch.batch_id, owner_id)
        attacker_view = await batch_repo.get(conn, batch.batch_id, attacker_id)
        missing_view = await batch_repo.get(conn, uuid.uuid4(), attacker_id)

    assert owner_view is not None and owner_view.tenant_id == owner_id, (
        "결함 전제(배치가 존재하고 tenant A 소유)가 재현되지 않았습니다"
    )
    assert attacker_view is None, (
        "BatchRepository.get()이 tenant를 구분하지 않아 attacker_id로도 "
        "tenant A의 배치가 조회됩니다(§8.3 '404 동형' 위반)"
    )
    assert missing_view is None, (
        "다른 tenant 소유 배치와 존재하지 않는 배치가 같은 None으로 "
        "동형이어야 한다(§8.3 '존재 자체 비노출')"
    )


# ---- DEEPEN(task-2985): negative #2 -- get_tick_batch()도 동일 규칙 ----


async def test_cross_tenant_get_tick_batch_does_not_leak_existence(pool, batch_repo):
    """negative #2(D3): `get()`과 동일한 tenant 격리 규칙(§8.3 LA-21)이
    형제 메서드 `get_tick_batch()`(LA-16a, 별도 테이블 `md_ingest_batch_tick`
    ·별도 WHERE 절)에도 그대로 적용되는지 검증한다 — negative #1은 이
    회귀 표면을 전혀 건드리지 않았다."""
    owner_id = await create_test_tenant(pool)
    attacker_id = await create_test_tenant(pool)

    async with pool.acquire() as conn, conn.transaction():
        instrument_id = await _instrument_id(conn)
        batch = await _seed_owner_tick_batch(
            conn, batch_repo, tenant_id=owner_id, instrument_id=instrument_id
        )

    async with pool.acquire() as conn:
        owner_view = await batch_repo.get_tick_batch(conn, batch.batch_id, owner_id)
        attacker_view = await batch_repo.get_tick_batch(conn, batch.batch_id, attacker_id)
        missing_view = await batch_repo.get_tick_batch(conn, uuid.uuid4(), attacker_id)

    assert owner_view is not None and owner_view.tenant_id == owner_id
    assert attacker_view is None, (
        "get_tick_batch()이 tenant를 구분하지 않아 attacker_id로도 "
        "owner의 틱 배치가 조회됩니다(§8.3 '404 동형' 위반)"
    )
    assert missing_view is None


# ---- DEEPEN(task-2985): negative #3 -- tenant_id=None(인증 컨텍스트 유실) ----


async def test_owner_batch_not_leaked_via_null_tenant_id_query(pool, batch_repo, candle_store):
    """negative #3(D3): 호출부가 인증 컨텍스트를 잃고 `tenant_id=None`으로
    질의하는 사고(예: 미들웨어가 tenant 추출에 실패해 기본값 None을 넘김)를
    흉내낸다. `IS NOT DISTINCT FROM`은 NULL과 실제 tenant_id를 동등으로
    보지 않으므로, 실제 tenant가 소유한 배치는 `tenant_id=None` 질의로는
    보이지 않아야 한다 — negative #1(공격자의 *진짜* tenant_id)과 반대
    방향("인증 실패 시 기본으로 열람 허용"이 되는 fail-open)을 막는다."""
    owner_id = await create_test_tenant(pool)
    async with pool.acquire() as conn, conn.transaction():
        instrument_id = await _instrument_id(conn)
        batch = await _seed_owner_batch(
            conn, batch_repo, candle_store, tenant_id=owner_id, instrument_id=instrument_id
        )

    async with pool.acquire() as conn:
        anonymous_view = await batch_repo.get(conn, batch.batch_id, None)

    assert anonymous_view is None, (
        "tenant_id=None 질의로 실제 tenant 소유 배치가 조회됩니다 -- "
        "인증 컨텍스트 유실이 열람 허용으로 이어지는 fail-open 결함입니다"
    )


# ---- DEEPEN(task-2985): failure-injection ----


async def test_get_propagates_connection_failure_instead_of_silently_returning_none(
    pool, batch_repo, candle_store, monkeypatch
):
    """failure-injection(D3): `md_ingest_batch`는 append-only(WORM) 트리거
    (`4a1d0c0de001`, `worm_sql()`)가 UPDATE/DELETE를 전부 막아 tenant_id를
    직접 SQL로 손상시킬 수 없다 -- 그래서 이 테스트는 커넥션 계층 장애를
    주입한다. `get()`이 tenant 필터 미스매치(정상적으로 의도된 `None`)와
    실제 DB 장애를 구분하지 못하고 장애까지 조용히 `None`으로 삼키면,
    운영 관점에서 "다른 tenant 소유"와 "DB가 죽어서 확인 불가"가 똑같이
    보여 인시던트가 은폐된다. `conn.fetchrow`가 예외를 던지면 `get()`은
    그 예외를 그대로 전파해야 한다(swallow 금지)."""
    owner_id = await create_test_tenant(pool)
    async with pool.acquire() as conn, conn.transaction():
        instrument_id = await _instrument_id(conn)
        batch = await _seed_owner_batch(
            conn, batch_repo, candle_store, tenant_id=owner_id, instrument_id=instrument_id
        )

    async def _boom(*_args: object, **_kwargs: object) -> None:
        raise asyncpg.exceptions.ConnectionDoesNotExistError("simulated connection loss")

    monkeypatch.setattr(asyncpg.connection.Connection, "fetchrow", _boom)
    async with pool.acquire() as conn:
        with pytest.raises(asyncpg.exceptions.ConnectionDoesNotExistError):
            await batch_repo.get(conn, batch.batch_id, owner_id)


# ---- DEEPEN(task-2985): 수치 성능 단언 ----


@pytest.mark.perf
async def test_cross_tenant_denied_get_short_circuits_without_reconstruction_round_trips(
    pool, batch_repo, candle_store
):
    """수치 성능 단언(D3): tenant 필터에 걸린 거부 조회는 `_reconstruct_
    verdict`/`_stored_range`(candle/quarantine/quality_issue 집계, 최대
    4회 추가 왕복)를 전혀 유발하지 않고 즉시 `None`을 반환해야 한다 --
    그렇지 않으면 owner 조회와 같은 만큼 일하게 되어 응답 시간으로 배치
    존재 여부를 추론하는 타이밍 부채널이 열리고, 무단 스캔의 DB 부하도
    owner 조회와 같아진다. 절대시간이 아니라 쿼리 횟수로 단언해 공유 CI
    지연 배율에 영향받지 않는다(task-2987 round-trip-count 선례와 동일
    원칙)."""
    owner_id = await create_test_tenant(pool)
    attacker_id = await create_test_tenant(pool)

    async with pool.acquire() as conn, conn.transaction():
        instrument_id = await _instrument_id(conn)
        batch = await _seed_owner_batch(
            conn, batch_repo, candle_store, tenant_id=owner_id, instrument_id=instrument_id
        )

    async with pool.acquire() as conn:
        denied_queries: list[str] = []
        denied_logger = _query_logger(denied_queries)
        conn.add_query_logger(denied_logger)
        try:
            attacker_view = await batch_repo.get(conn, batch.batch_id, attacker_id)
        finally:
            conn.remove_query_logger(denied_logger)

        owner_queries: list[str] = []
        owner_logger = _query_logger(owner_queries)
        conn.add_query_logger(owner_logger)
        try:
            owner_view = await batch_repo.get(conn, batch.batch_id, owner_id)
        finally:
            conn.remove_query_logger(owner_logger)

    print(
        f"\nmarket_data cross_tenant get() round trips: denied={len(denied_queries)} "
        f"owner={len(owner_queries)}"
    )
    assert attacker_view is None
    assert owner_view is not None
    assert len(denied_queries) == 1, (
        f"거부된 cross-tenant 조회가 {len(denied_queries)}회 왕복했습니다 -- 필터 실패 시 "
        "즉시 반환(1회)해야 재구성 비용에 의한 타이밍/부하 부채널이 생기지 않습니다"
    )
    assert len(owner_queries) > len(denied_queries), (
        "owner 조회가 거부된 조회보다 많은 왕복을 내야 short-circuit 경로가 "
        "실제로 더 짧다는 것이 증명됩니다"
    )


# ---- DEEPEN(task-2985): 이 리프 파일 내 게이트 적색 재현(I-10) ----


class _LeakyBatchRepository(PostgresBatchRepository):
    """게이트 적색 재현(D3, I-10): LA-22(task-825) 이전 결함(task-655)의
    최소 재현 -- `get()`에서 tenant_id 필터를 빼면 무슨 일이 일어나는지
    보여준다. 이 파일의 negative 테스트들이 실제로 이 결함을 탐지하는지
    (게이트가 "있다"가 아니라 "작동함")를 이 서브클래스로 증명한다."""

    async def get(
        self, conn: asyncpg.Connection, batch_id: uuid.UUID, tenant_id: uuid.UUID | None
    ) -> IngestBatchResult | None:  # noqa: ARG002 -- tenant_id를 의도적으로 무시(결함 재현)
        row = await conn.fetchrow("SELECT * FROM md_ingest_batch WHERE id = $1", batch_id)
        if row is None:
            return None

        verdict = await _reconstruct_verdict(conn, row)
        stored_range = await _stored_range(conn, batch_id)
        return IngestBatchResult(
            batch_id=row["id"],
            tenant_id=row["tenant_id"],
            source=row["source"],
            venue=Venue(row["venue"]),
            instrument_id=row["instrument_id"],
            timeframe=Timeframe(row["timeframe"]),
            range_start=row["range_start"],
            range_end=row["range_end"],
            request_fingerprint=row["request_fingerprint"],
            verdict=verdict,
            batch_hash=row["batch_hash"],
            audit_event_id=row["audit_event_id"],
            stored_range=stored_range,
        )


async def test_leaky_repository_without_tenant_filter_reproduces_task_655_cross_tenant_leak(
    pool, candle_store
):
    """게이트 적색 재현(D3, I-10): 위 negative 테스트들이 실제로 방어하는
    결함(task-655)을 의도적으로 재현한 `_LeakyBatchRepository`로 증명한다
    -- 이 서브클래스에 같은 시나리오를 돌리면 attacker_view가 owner의
    배치를 그대로 받는다(게이트 적색). 즉 이 파일의 assert들이 실제로
    tenant_id 필터 유무를 구분해 낸다는 증거다."""
    leaky_repo = _LeakyBatchRepository(pool)
    owner_id = await create_test_tenant(pool)
    attacker_id = await create_test_tenant(pool)

    async with pool.acquire() as conn, conn.transaction():
        instrument_id = await _instrument_id(conn)
        batch = await _seed_owner_batch(
            conn, leaky_repo, candle_store, tenant_id=owner_id, instrument_id=instrument_id
        )

    async with pool.acquire() as conn:
        attacker_view = await leaky_repo.get(conn, batch.batch_id, attacker_id)

    assert attacker_view is not None and attacker_view.tenant_id == owner_id, (
        "게이트 적색 재현 실패 -- _LeakyBatchRepository도 tenant를 격리한다면 "
        "이 파일의 negative 테스트들이 실제로 무엇을 방지하는지 증명하지 못합니다"
    )


# ---- DEEPEN(task-2985): 적대적 replay(D3) ----


async def test_replayed_cross_tenant_probe_always_returns_none(pool, batch_repo, candle_store):
    """적대적 replay(D3): 공격자가 같은 batch_id를 반복 조회하는 자동화된
    스캔/재시도를 흉내낸다. 캐시나 커넥션 재사용 경로에 상태가 새어
    첫 시도 이후 결과가 달라지는 회귀가 없는지 20회 반복으로 확인한다."""
    owner_id = await create_test_tenant(pool)
    attacker_id = await create_test_tenant(pool)

    async with pool.acquire() as conn, conn.transaction():
        instrument_id = await _instrument_id(conn)
        batch = await _seed_owner_batch(
            conn, batch_repo, candle_store, tenant_id=owner_id, instrument_id=instrument_id
        )

    for attempt in range(20):
        async with pool.acquire() as conn:
            replayed_view = await batch_repo.get(conn, batch.batch_id, attacker_id)
        assert replayed_view is None, (
            f"{attempt + 1}번째 replay에서 cross-tenant 배치가 노출됐습니다"
        )


# ---- DEEPEN(task-2985): 동시성(D3) ----


async def test_concurrent_owner_and_attacker_get_calls_isolated_via_pool(
    pool, batch_repo, candle_store
):
    """동시성(D3): 같은 batch_id에 대해 owner/attacker 요청을 여러 pool
    커넥션으로 동시에 섞어 보내, 커넥션 재사용(pool)이 tenant 컨텍스트를
    엇갈리게 캐싱/누출하지 않는지 검증한다."""
    owner_id = await create_test_tenant(pool)
    attacker_id = await create_test_tenant(pool)

    async with pool.acquire() as conn, conn.transaction():
        instrument_id = await _instrument_id(conn)
        batch = await _seed_owner_batch(
            conn, batch_repo, candle_store, tenant_id=owner_id, instrument_id=instrument_id
        )

    async def _owner_call() -> IngestBatchResult | None:
        async with pool.acquire() as conn:
            return await batch_repo.get(conn, batch.batch_id, owner_id)

    async def _attacker_call() -> IngestBatchResult | None:
        async with pool.acquire() as conn:
            return await batch_repo.get(conn, batch.batch_id, attacker_id)

    calls = [_owner_call() if i % 2 == 0 else _attacker_call() for i in range(10)]
    results = await asyncio.gather(*calls)

    for i, result in enumerate(results):
        if i % 2 == 0:
            assert result is not None and result.tenant_id == owner_id, (
                f"동시 조회 {i}번째(owner)가 격리되지 않았습니다"
            )
        else:
            assert result is None, f"동시 조회 {i}번째(attacker)가 격리되지 않았습니다"
