"""PostgresCoverageRepository 통합테스트 — 실 DB(TEST_DATABASE_URL) 대상.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§9.2 DC-8. DoD: EXCLUDE 위반이 실 DB INSERT로 단언되고, 조회가 다른
instrument/timeframe의 coverage를 반환하지 않으며, 커버리지가 없는 구간
질의가 빈 리스트로 정확히 구분됨(0/NaN으로 채우지 않음, §4.1/§6).
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timedelta, timezone

import asyncpg
import pytest

from src.foundation.market_data.adapters.postgres_coverage_repository import (
    CoverageSpanOverlapError,
    PostgresCoverageRepository,
)
from src.foundation.market_data.contracts.v1 import Timeframe, Venue
from src.foundation.market_data.ports.coverage_repository import CoverageQuality, CoverageSpan


def _fake_ulid() -> str:
    return "0" + uuid.uuid4().hex[:25].upper()


async def _insert_instrument(pool: asyncpg.Pool, instrument_id: str) -> None:
    await pool.execute(
        """
        INSERT INTO instruments (
            instrument_id, asset_class, base, quote, isin, figi,
            tick_size, lot_size, calendar_id, lifecycle_state
        ) VALUES ($1, 'CRYPTO', 'BTC', 'USDT', NULL, NULL, 0.01, 0.0001, '24x7', 'ACTIVE')
        """,
        instrument_id,
    )


def _span(*, instrument_id: str, start: datetime, end: datetime, **overrides) -> CoverageSpan:
    fields = {
        "instrument_id": instrument_id,
        "venue": Venue.BITGET,
        "timeframe": Timeframe.M1,
        "quality": CoverageQuality.PROVISIONAL,
        "start": start,
        "end": end,
    }
    fields.update(overrides)
    return CoverageSpan(**fields)


@pytest.fixture
def repo(pool):
    return PostgresCoverageRepository(pool)


async def test_upsert_then_list_spans_round_trips(pool, repo):
    instrument_id = _fake_ulid()
    await _insert_instrument(pool, instrument_id)
    t0 = datetime.now(timezone.utc) - timedelta(days=10)

    async with pool.acquire() as conn, conn.transaction():
        saved = await repo.upsert_span(
            conn, _span(instrument_id=instrument_id, start=t0, end=t0 + timedelta(days=5))
        )
    assert saved.instrument_id == instrument_id

    async with pool.acquire() as conn, conn.transaction():
        spans = await repo.list_spans(conn, instrument_id, Timeframe.M1)
    assert len(spans) == 1
    assert spans[0].start == t0


async def test_upsert_overlapping_span_raises(pool, repo):
    """negative: 파이썬 선검사가 아니라 실DB INSERT로 EXCLUDE 위반을 단언."""
    instrument_id = _fake_ulid()
    await _insert_instrument(pool, instrument_id)
    t0 = datetime.now(timezone.utc) - timedelta(days=10)

    async with pool.acquire() as conn, conn.transaction():
        await repo.upsert_span(
            conn, _span(instrument_id=instrument_id, start=t0, end=t0 + timedelta(days=5))
        )

    with pytest.raises(CoverageSpanOverlapError):
        async with pool.acquire() as conn, conn.transaction():
            await repo.upsert_span(
                conn,
                _span(
                    instrument_id=instrument_id,
                    start=t0 + timedelta(days=2),
                    end=t0 + timedelta(days=8),
                ),
            )


async def test_list_spans_does_not_return_other_instruments_coverage(pool, repo):
    """negative: instrument_id로 스코프한 조회가 다른 instrument의 coverage를
    반환하면 안 된다(DoD: 남의 coverage를 반환하지 않음)."""
    instrument_a = _fake_ulid()
    instrument_b = _fake_ulid()
    await _insert_instrument(pool, instrument_a)
    await _insert_instrument(pool, instrument_b)
    t0 = datetime.now(timezone.utc) - timedelta(days=10)

    async with pool.acquire() as conn, conn.transaction():
        await repo.upsert_span(
            conn, _span(instrument_id=instrument_a, start=t0, end=t0 + timedelta(days=5))
        )
    async with pool.acquire() as conn, conn.transaction():
        await repo.upsert_span(
            conn, _span(instrument_id=instrument_b, start=t0, end=t0 + timedelta(days=5))
        )

    async with pool.acquire() as conn, conn.transaction():
        spans_a = await repo.list_spans(conn, instrument_a, Timeframe.M1)
    assert len(spans_a) == 1
    assert spans_a[0].instrument_id == instrument_a


async def test_list_spans_does_not_return_other_timeframes_coverage(pool, repo):
    instrument_id = _fake_ulid()
    await _insert_instrument(pool, instrument_id)
    t0 = datetime.now(timezone.utc) - timedelta(days=10)

    async with pool.acquire() as conn, conn.transaction():
        await repo.upsert_span(
            conn,
            _span(
                instrument_id=instrument_id,
                timeframe=Timeframe.M1,
                start=t0,
                end=t0 + timedelta(days=5),
            ),
        )
    async with pool.acquire() as conn, conn.transaction():
        await repo.upsert_span(
            conn,
            _span(
                instrument_id=instrument_id,
                timeframe=Timeframe.H1,
                start=t0,
                end=t0 + timedelta(days=5),
            ),
        )

    async with pool.acquire() as conn, conn.transaction():
        spans_m1 = await repo.list_spans(conn, instrument_id, Timeframe.M1)
    assert len(spans_m1) == 1
    assert spans_m1[0].timeframe == Timeframe.M1


async def test_list_spans_for_uncovered_instrument_returns_empty_not_full_coverage(pool, repo):
    """§4.1/§6: 커버리지 선언이 전혀 없는 instrument×timeframe 질의는 빈
    리스트를 반환해야 한다 — 이것이 "커버됨"으로 오인되면 안 되고, 호출자가
    DATA_COVERAGE_MISSING으로 fail-closed 판정할 근거가 된다. 조용히 0으로
    채우거나 임의의 커버리지 행을 만들어내지 않는다."""
    uncovered_instrument = _fake_ulid()
    await _insert_instrument(pool, uncovered_instrument)

    async with pool.acquire() as conn, conn.transaction():
        spans = await repo.list_spans(conn, uncovered_instrument, Timeframe.M1)

    assert spans == []


async def test_upsert_span_with_end_before_start_raises(pool, repo):
    """negative: `end <= start`인 구간은 DB CHECK(end_at > start_at)로
    거부되어야 한다 — 어댑터가 파이썬에서 먼저 걸러내지 않아도 fail-closed
    로 DB가 막는다(9049e2b6b0b7 마이그레이션 CHECK 제약)."""
    instrument_id = _fake_ulid()
    await _insert_instrument(pool, instrument_id)
    t0 = datetime.now(timezone.utc) - timedelta(days=10)

    with pytest.raises(asyncpg.exceptions.CheckViolationError):
        async with pool.acquire() as conn, conn.transaction():
            await repo.upsert_span(
                conn, _span(instrument_id=instrument_id, start=t0, end=t0 - timedelta(hours=1))
            )


async def test_upsert_span_with_unknown_instrument_raises(pool, repo):
    """negative: `instruments`에 존재하지 않는 instrument_id로 커버리지를
    선언하면 FK 위반으로 거부되어야 한다 — 존재하지 않는 종목의 커버리지가
    조용히 저장되면 안 된다."""
    unknown_instrument = _fake_ulid()
    t0 = datetime.now(timezone.utc) - timedelta(days=10)

    with pytest.raises(asyncpg.exceptions.ForeignKeyViolationError):
        async with pool.acquire() as conn, conn.transaction():
            await repo.upsert_span(
                conn,
                _span(instrument_id=unknown_instrument, start=t0, end=t0 + timedelta(days=5)),
            )


async def test_upsert_span_unexpected_db_error_propagates_unmapped(pool, repo, monkeypatch):
    """실패주입: `ExclusionViolationError`가 아닌 예상치 못한 DB 에러(예:
    커넥션 단절)는 `CoverageSpanOverlapError`로 오분류되지 않고 원본 그대로
    전파되어야 한다 — 겹침이 아닌 장애를 겹침으로 잘못 보고하면 호출자가
    잘못된 복구 경로(재병합)를 타게 된다."""
    instrument_id = _fake_ulid()
    await _insert_instrument(pool, instrument_id)
    t0 = datetime.now(timezone.utc) - timedelta(days=10)

    async def _boom(*args, **kwargs):
        raise asyncpg.exceptions.ConnectionDoesNotExistError("simulated connection loss")

    monkeypatch.setattr(asyncpg.connection.Connection, "fetchrow", _boom)
    async with pool.acquire() as conn, conn.transaction():
        with pytest.raises(asyncpg.exceptions.ConnectionDoesNotExistError):
            await repo.upsert_span(
                conn, _span(instrument_id=instrument_id, start=t0, end=t0 + timedelta(days=5))
            )


@pytest.mark.perf
@pytest.mark.nightly
async def test_list_spans_p95_latency_within_budget(pool, repo):
    """성능 단언: 커버리지 조회는 market_data 읽기 경로 예산(ADR-2026-09-09-C
    Decision 1, "5k봉 조회 p95 200ms")을 상한으로 삼는다 — coverage_spans는
    캔들보다 훨씬 가벼운 행 수이므로 같은 예산 안에서 p95가 나워야 한다.

    task-9249 추적(task-9629): perf 테스트는 CI 공유 환경에서 커버리지 측정 오버헤드로
    인해 예상 소요 시간을 초과할 수 있으므로 nightly로 표시하여 기본 실행에서 제외한다."""
    instrument_id = _fake_ulid()
    await _insert_instrument(pool, instrument_id)
    base = datetime.now(timezone.utc) - timedelta(days=400)

    async with pool.acquire() as conn, conn.transaction():
        for i in range(50):
            start = base + timedelta(days=i * 2)
            await repo.upsert_span(
                conn,
                _span(instrument_id=instrument_id, start=start, end=start + timedelta(days=1)),
            )

    samples: list[float] = []
    for _ in range(20):
        t_start = time.perf_counter()
        async with pool.acquire() as conn, conn.transaction():
            spans = await repo.list_spans(conn, instrument_id, Timeframe.M1)
        samples.append(time.perf_counter() - t_start)
    assert len(spans) == 50

    samples.sort()
    p95_ms = samples[int(len(samples) * 0.95) - 1] * 1000
    assert p95_ms < 200, f"list_spans p95={p95_ms:.1f}ms exceeds 200ms budget"
