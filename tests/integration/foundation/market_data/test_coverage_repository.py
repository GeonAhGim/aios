"""PostgresCoverageRepository 통합테스트 — 실 DB(TEST_DATABASE_URL) 대상.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§9.2 DC-8. DoD: EXCLUDE 위반이 실 DB INSERT로 단언되고, 조회가 다른
instrument/timeframe의 coverage를 반환하지 않으며, 커버리지가 없는 구간
질의가 빈 리스트로 정확히 구분됨(0/NaN으로 채우지 않음, §4.1/§6).
"""
from __future__ import annotations

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
                instrument_id=instrument_id, timeframe=Timeframe.M1,
                start=t0, end=t0 + timedelta(days=5),
            ),
        )
    async with pool.acquire() as conn, conn.transaction():
        await repo.upsert_span(
            conn,
            _span(
                instrument_id=instrument_id, timeframe=Timeframe.H1,
                start=t0, end=t0 + timedelta(days=5),
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
