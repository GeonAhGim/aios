"""LA-17 `application/get_candles.get_candles` 통합테스트 — 실 DB(TEST_DATABASE_URL).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9.2 LA-17.
DoD(task-624): 조정계수는 as_of 시점 기준으로만 반영, 갭은 정보로만 반환(비
strict), negative: 미등록 instrument → 명시적 에러.

BITGET(연속 세션)만 쓴다 — `VenueCalendar`(LA-3) 휴장일 적재 없이도 세션
판정이 가능해 캘린더 시드가 필요 없다(get_candles.py의 `_sessions_for_range`
가 연속 venue는 `CalendarRepository`를 아예 호출하지 않는다).

task-10929: 실패주입(D3)은 `test_get_candles_failure_injection.py`로,
수치 성능 단언(D3)은 `test_get_candles_perf.py`로 분리했다(loc_over_500).
시드 헬퍼는 `get_candles_support.py`, 공용 픽스처는 `conftest.py`에 있다.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.foundation.market_data.application.get_candles import (
    AsOfInFutureError,
    QuarantinedViewUnsupportedError,
    UnknownSeriesError,
    get_candles,
)
from src.foundation.market_data.contracts.v1 import (
    Adjustment,
    CandleQuery,
    CorporateAction,
    SeriesKey,
    Timeframe,
    Venue,
)
from tests.integration.foundation.market_data.get_candles_support import (
    candle_open_time_before_today,
    seed_candle,
    utc_minute_now,
)
from tests.integration.foundation.market_data.get_candles_support import (
    instrument_id as _instrument_id,
)


async def test_get_candles_returns_raw_series_with_hash_and_no_gaps(
    pool, candle_store, batch_repo, reference_repo, calendar_repo
):
    async with pool.acquire() as conn, conn.transaction():
        instrument_id = await _instrument_id(conn)
        t0 = utc_minute_now()
        key = await seed_candle(
            conn, batch_repo, candle_store, instrument_id=instrument_id, open_time=t0
        )

    query = CandleQuery(key=key, start=t0, end=t0 + timedelta(minutes=1))
    series = await get_candles(
        query, store=candle_store, refs=reference_repo, cal=calendar_repo, pool=pool
    )
    assert [c.open_time for c in series.candles] == [t0]
    assert series.gaps == []
    assert series.adjustment is Adjustment.RAW
    assert len(series.series_hash) == 64


async def test_get_candles_as_of_equal_to_close_time_includes_candle(
    pool, candle_store, batch_repo, reference_repo, calendar_repo
):
    """경계 계약 고정(AUDIT_2026-10-01 F5): `as_of`가 캔들의 `close_time`과
    정확히 같으면 그 캔들은 포함된다. 실제 필터는 `created_at <= as_of`(WORM
    스냅샷, `postgres_candle_store.query`)로 동작한다 — `md_candle`은
    append-only라 `created_at`을 사후에 바꿀 수 없으므로(UPDATE 거부), 삽입
    시점에 `created_at`을 명시적으로 `close_time`과 같게 넣어 `created_at <=
    as_of`가 성립하는 상태에서 `as_of == close_time` 경계만을 검증한다."""
    import uuid

    from src.foundation.market_data.contracts.v1 import (
        IngestBatchResult,
        QualityVerdict,
        Verdict,
    )
    from tests.integration.foundation.market_data.get_candles_support import audit_event_id

    async with pool.acquire() as conn, conn.transaction():
        instrument_id = await _instrument_id(conn)
        t0 = (datetime.now(timezone.utc) - timedelta(minutes=2)).replace(second=0, microsecond=0)
        close_time = t0 + timedelta(minutes=1)
        key = SeriesKey(venue=Venue.BITGET, instrument_id=instrument_id, timeframe=Timeframe.M1)
        event_id = await audit_event_id(conn)
        batch = IngestBatchResult(
            batch_id=uuid.uuid4(),
            source="test",
            venue=Venue.BITGET,
            instrument_id=instrument_id,
            timeframe=Timeframe.M1,
            range_start=t0,
            range_end=close_time,
            request_fingerprint=f"fp-{uuid.uuid4().hex}",
            verdict=QualityVerdict(
                verdict=Verdict.ACCEPT, accepted=1, quarantined=0, rejected=0, issues=[]
            ),
            batch_hash=f"hash-{uuid.uuid4().hex}",
            audit_event_id=event_id,
            stored_range=None,
        )
        await batch_repo.create(conn, batch)
        # `created_at`을 `close_time`과 같게 명시 삽입 — WORM이라 삽입 후에는
        # 바꿀 수 없다(`UPDATE`는 트리거가 거부).
        await conn.execute(
            "INSERT INTO md_candle "
            "(venue, instrument_id, timeframe, open_time, close_time, open, high, low, "
            " close, volume, batch_id, created_at) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $5)",
            key.venue.value,
            key.instrument_id,
            key.timeframe.value,
            t0,
            close_time,
            Decimal("100"),
            Decimal("110"),
            Decimal("90"),
            Decimal("105"),
            Decimal("10"),
            batch.batch_id,
        )

    query = CandleQuery(key=key, start=t0, end=close_time, as_of=close_time)
    series = await get_candles(
        query, store=candle_store, refs=reference_repo, cal=calendar_repo, pool=pool
    )
    assert [c.open_time for c in series.candles] == [t0], (
        "as_of == close_time인 캔들은 경계 포함(contract)"
    )


async def test_get_candles_reports_gap_without_raising(
    pool, candle_store, batch_repo, reference_repo, calendar_repo
):
    async with pool.acquire() as conn, conn.transaction():
        instrument_id = await _instrument_id(conn)
        t0 = utc_minute_now()
        key = await seed_candle(
            conn, batch_repo, candle_store, instrument_id=instrument_id, open_time=t0
        )

    # t0+1분은 결측 — 3분짜리 창 안에 캔들 1개(t0)만 있으므로 갭 1구간.
    query = CandleQuery(key=key, start=t0, end=t0 + timedelta(minutes=3))
    series = await get_candles(
        query, store=candle_store, refs=reference_repo, cal=calendar_repo, pool=pool
    )
    assert series.gaps == [(t0 + timedelta(minutes=1), t0 + timedelta(minutes=3))]


async def test_get_candles_adjustment_ignores_action_after_as_of(
    pool, candle_store, batch_repo, reference_repo, calendar_repo
):
    """negative: `ex_date`가 `as_of`보다 미래인 조정은 `factor_chain`(LA-8)이
    제외한다 — as_of 시점에는 아직 일어나지 않은 조정이므로."""
    async with pool.acquire() as conn, conn.transaction():
        instrument_id = await _instrument_id(conn)
        as_of = (await conn.fetchval("SELECT now()")).astimezone(timezone.utc)
        # 월초로 고정 — md_candle 파티션은 현재~+N개월만 존재하므로(과거 달
        # 파티션 없음) 이번 달 안에서만 "과거" 캔들을 만든다.
        t0 = as_of.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        key = await seed_candle(
            conn, batch_repo, candle_store, instrument_id=instrument_id, open_time=t0
        )
        await reference_repo.record_action(
            conn,
            CorporateAction(
                action_type="SPLIT",
                instrument_id=instrument_id,
                ex_date=as_of.date() + timedelta(days=1),
                ratio=Decimal(2),
                source_ref="test:future",
            ),
        )

    query = CandleQuery(
        key=key,
        start=t0,
        end=t0 + timedelta(minutes=1),
        as_of=as_of,
        adjustment=Adjustment.ADJUSTED,
    )
    series = await get_candles(
        query, store=candle_store, refs=reference_repo, cal=calendar_repo, pool=pool
    )
    assert series.candles[0].open == Decimal("100"), "as_of보다 미래인 ex_date는 미반영"


async def test_get_candles_adjustment_applies_action_before_as_of(
    pool, candle_store, batch_repo, reference_repo, calendar_repo
):
    """`ex_date`가 `as_of` 이전(<=)이고 캔들 날짜보다는 이후면 조정이 반영된다."""
    async with pool.acquire() as conn, conn.transaction():
        instrument_id = await _instrument_id(conn)
        as_of = (await conn.fetchval("SELECT now()")).astimezone(timezone.utc)
        # 월초로 고정(위 테스트와 같은 이유) — ex_date는 캔들(월초)보다는
        # 늦고 as_of(오늘)보다는 이르거나 같아야 하므로 오늘 날짜를 쓴다.
        t0 = await candle_open_time_before_today(conn, as_of)
        key = await seed_candle(
            conn, batch_repo, candle_store, instrument_id=instrument_id, open_time=t0
        )
        await reference_repo.record_action(
            conn,
            CorporateAction(
                action_type="SPLIT",
                instrument_id=instrument_id,
                ex_date=as_of.date(),
                ratio=Decimal(2),
                source_ref="test:past",
            ),
        )

    query = CandleQuery(
        key=key,
        start=t0,
        end=t0 + timedelta(minutes=1),
        as_of=as_of,
        adjustment=Adjustment.ADJUSTED,
    )
    series = await get_candles(
        query, store=candle_store, refs=reference_repo, cal=calendar_repo, pool=pool
    )
    assert series.candles[0].open == Decimal("50"), "as_of 이전 ex_date 조정은 반영되어야 한다"
    assert series.candles[0].volume == Decimal("20")


async def test_get_candles_unknown_series_raises(pool, candle_store, reference_repo, calendar_repo):
    """negative: 한 번도 수집된 적 없는 (venue, instrument, timeframe) → 명시적 에러."""
    async with pool.acquire() as conn, conn.transaction():
        instrument_id = await _instrument_id(conn)
    key = SeriesKey(venue=Venue.BITGET, instrument_id=instrument_id, timeframe=Timeframe.M1)
    t0 = utc_minute_now()
    query = CandleQuery(key=key, start=t0, end=t0 + timedelta(minutes=1))
    with pytest.raises(UnknownSeriesError):
        await get_candles(
            query, store=candle_store, refs=reference_repo, cal=calendar_repo, pool=pool
        )


async def test_get_candles_as_of_in_future_raises(
    pool, candle_store, reference_repo, calendar_repo
):
    """negative: `as_of`가 현재보다 미래면 조회 전에 즉시 거부한다(스토어를
    건드리지 않으므로 미등록 instrument여도 무방)."""
    import uuid

    key = SeriesKey(venue=Venue.BITGET, instrument_id=uuid.uuid4(), timeframe=Timeframe.M1)
    t0 = utc_minute_now()
    future_as_of = t0 + timedelta(days=1)
    query = CandleQuery(key=key, start=t0, end=t0 + timedelta(minutes=1), as_of=future_as_of)
    with pytest.raises(AsOfInFutureError):
        await get_candles(
            query, store=candle_store, refs=reference_repo, cal=calendar_repo, pool=pool
        )


async def test_get_candles_quarantined_view_unsupported_raises(
    pool, candle_store, batch_repo, reference_repo, calendar_repo
):
    """negative: `CandleStore.query`(LA-13)가 지원하지 않는 파라미터는 조용히
    무시하지 않고 명시적으로 거부한다."""
    async with pool.acquire() as conn, conn.transaction():
        instrument_id = await _instrument_id(conn)
        t0 = utc_minute_now()
        key = await seed_candle(
            conn, batch_repo, candle_store, instrument_id=instrument_id, open_time=t0
        )

    query = CandleQuery(
        key=key,
        start=t0,
        end=t0 + timedelta(minutes=1),
        include_quarantined=True,
    )
    with pytest.raises(QuarantinedViewUnsupportedError):
        await get_candles(
            query, store=candle_store, refs=reference_repo, cal=calendar_repo, pool=pool
        )
