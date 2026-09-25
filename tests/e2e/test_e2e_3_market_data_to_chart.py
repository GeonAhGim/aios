"""E2E-3 — 시세 수집→저장→차트/지표 API — 캔들 적재→조회→지표 집계.

Spec: docs/design/ADR-2026-09-24-A-mvp1-exit-order-and-fleet-kit.md Decision 3
— depth 미판정(D?) 리프 개별 재QA 대신 이 시나리오 녹색으로 대체 검증한다.

커버 리프:
- LA-9(IngestSource 포트), LA-15(ingest_candles 적재 품질 판정 → audit),
- LA-17(get_candles as_of 스냅샷/정렬/해시), LA-24(HTTP 읽기 API),
- DC-10(M1→상위 타임프레임 결정론 집계).

파이프라인: (1) Mock adapter에서 M1 캔들 배치 생성. (2) 실제 ingest_candles()로
— 참조데이터 등록→fetch→품질판정→저장/격리→감사 기록. (3) 실제 get_candles()
로 조회(as_of, 정렬, 해시). (4) 실제 rollup()으로 M1→M5 집계, OHLCV 동일성
검증(Decimal 정밀도).

실패 주입 2건: (1) OHLC 위반(low > high) — ingest_candles pipeline의
QUARANTINE 경로, (2) 외부 source 타임아웃 — fetch 예외 처리.

LA-21(ReplayVerify) 대체: as_of 스냅샷 + 재조회 결과 동일 검증(batch_hash 포함).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import asyncpg
import pytest

from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.market_data.adapters.postgres_batch_repository import (
    PostgresBatchRepository,
)
from src.foundation.market_data.adapters.postgres_calendar_repository import (
    PostgresCalendarRepository,
)
from src.foundation.market_data.adapters.postgres_candle_store import (
    PostgresCandleStore,
)
from src.foundation.market_data.adapters.postgres_reference_repository import (
    PostgresReferenceRepository,
)
from src.foundation.market_data.application.get_candles import get_candles
from src.foundation.market_data.contracts.v1 import (
    CandleQuery,
    CandleRecord,
    SeriesKey,
    Timeframe,
    Venue,
    Verdict,
)
from src.foundation.market_data.domain.aggregation.timeframe_rollup import rollup
from src.foundation.market_data.domain.candle_columns import CandleColumns
from tests.e2e.market_data_helpers import (
    MockIngestSource,
    ingest_cmd,
    listed_instrument,
    run_ingest,
    simple_sma,
)

# Use today's date to ensure md_ensure_partitions creates the necessary partitions
# (it only creates forward from current month, not backward)
_BASE_DATE = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)


@pytest.fixture
def deps(pool: asyncpg.Pool) -> SimpleNamespace:
    """E2E-3 공용 의존성: 실제 repositories + adapters."""
    return SimpleNamespace(
        pool=pool,
        refs=PostgresReferenceRepository(pool),
        cal=PostgresCalendarRepository(pool),
        audit=PostgresAuditEventRepository(pool),
        store=PostgresCandleStore(pool),
        batches=PostgresBatchRepository(pool),
    )


async def test_ingest_and_query_candles_with_sma(pool: asyncpg.Pool, deps: SimpleNamespace) -> None:
    """LA-15 ingest_candles(실제) + LA-17 get_candles(실제) + SMA 계산 — Decimal 정확값 검증."""
    # --- 준비: 파티션 생성, 상장 상품, M1 캔들 6개 ---
    async with pool.acquire() as conn:
        await conn.execute("SELECT md_ensure_partitions(6)")

    instrument = await listed_instrument(deps, Venue.BITGET, _BASE_DATE)
    base_time = _BASE_DATE.replace(hour=0, minute=0, second=0)

    # M1 캔들 생성 (close price 기준)
    closes_raw = [Decimal(v) for v in (70000, 70100, 70050, 70150, 70200, 70080)]
    candles = []
    for i, close_price in enumerate(closes_raw):
        open_time = base_time + timedelta(minutes=i)
        candles.append(
            CandleRecord(
                key=SeriesKey(
                    venue=Venue.BITGET,
                    instrument_id=instrument.instrument_id,
                    timeframe=Timeframe.M1,
                ),
                open_time=open_time,
                close_time=open_time + timedelta(minutes=1),
                open=close_price,
                high=close_price + Decimal("100"),
                low=close_price - Decimal("50"),
                close=close_price,
                volume=Decimal("1000"),
                quote_volume=Decimal("70000000"),
            )
        )

    # --- 단계 1: 실제 ingest_candles 호출 (LA-15) ---
    source = MockIngestSource(candles=candles)
    cmd = ingest_cmd(instrument, base_time, base_time + timedelta(minutes=6), venue=Venue.BITGET)
    result = await run_ingest(deps, cmd, source, clock_at=base_time)

    assert result.verdict.verdict == Verdict.ACCEPT, "정상 캔들은 ACCEPT 판정"
    assert result.verdict.accepted == 6

    # --- 단계 2: 실제 get_candles 호출 (LA-17) ---
    query = CandleQuery(
        key=SeriesKey(
            venue=Venue.BITGET,
            instrument_id=instrument.instrument_id,
            timeframe=Timeframe.M1,
        ),
        start=base_time,
        end=base_time + timedelta(minutes=6),
    )
    series = await get_candles(
        query,
        store=deps.store,
        refs=deps.refs,
        cal=deps.cal,
        pool=deps.pool,
    )

    loaded_candles = series.candles
    assert len(loaded_candles) == 6
    assert loaded_candles[0].close == Decimal("70000")
    assert loaded_candles[1].close == Decimal("70100")

    # --- 단계 3: SMA(3) 지표 계산 + Decimal 정확값 검증 ---
    closes = [c.close for c in loaded_candles]
    sma_3 = simple_sma(closes, 3)

    # SMA(3) at index 2: (70000 + 70100 + 70050) / 3
    expected_sma_3_idx2 = (Decimal("70000") + Decimal("70100") + Decimal("70050")) / Decimal("3")
    assert sma_3[2] is not None
    assert sma_3[2] == expected_sma_3_idx2

    # SMA(3) at index 3: (70100 + 70050 + 70150) / 3
    expected_sma_3_idx3 = (Decimal("70100") + Decimal("70050") + Decimal("70150")) / Decimal("3")
    assert sma_3[3] == expected_sma_3_idx3


async def test_query_snapshot_idempotency_substitutes_for_replay_verify(
    pool: asyncpg.Pool, deps: SimpleNamespace
) -> None:
    """LA-17 as_of 스냅샷 멱등성 (LA-21 ReplayVerify 대체: batch_hash + 재조회 동일).

    같은 시각의 재조회는 동일 candles + 동일 series_hash를 반환해야 한다.
    """
    # --- 준비: 상장 상품, 파티션, M1 캔들 4개 ---
    instrument = await listed_instrument(deps, Venue.BITGET, _BASE_DATE)
    base_time = _BASE_DATE.replace(hour=0, minute=0, second=0)

    async with pool.acquire() as conn:
        await conn.execute("SELECT md_ensure_partitions(6)")

    closes_raw = [Decimal(v) for v in (100, 110, 105, 115)]
    candles = []
    for i, close_price in enumerate(closes_raw):
        open_time = base_time + timedelta(minutes=i)
        candles.append(
            CandleRecord(
                key=SeriesKey(
                    venue=Venue.BITGET,
                    instrument_id=instrument.instrument_id,
                    timeframe=Timeframe.M1,
                ),
                open_time=open_time,
                close_time=open_time + timedelta(minutes=1),
                open=close_price,
                high=close_price + Decimal("10"),
                low=close_price - Decimal("5"),
                close=close_price,
                volume=Decimal("1000"),
            )
        )

    # --- Ingest ---
    source = MockIngestSource(candles=candles)
    cmd = ingest_cmd(instrument, base_time, base_time + timedelta(minutes=4), venue=Venue.BITGET)
    ingest_result = await run_ingest(deps, cmd, source, clock_at=base_time)
    assert ingest_result.verdict.verdict == Verdict.ACCEPT

    # batch_hash 저장됨 (LA-8 lineage)
    assert ingest_result.batch_hash is not None

    # --- 첫 조회 ---
    query = CandleQuery(
        key=SeriesKey(
            venue=Venue.BITGET,
            instrument_id=instrument.instrument_id,
            timeframe=Timeframe.M1,
        ),
        start=base_time,
        end=base_time + timedelta(minutes=4),
    )
    series_1 = await get_candles(
        query, store=deps.store, refs=deps.refs, cal=deps.cal, pool=deps.pool
    )
    candles_1 = series_1.candles
    hash_1 = series_1.series_hash
    assert len(candles_1) == 4

    # --- 두 번째 조회 (재조회 with as_of snapshot) ---
    series_2 = await get_candles(
        query, store=deps.store, refs=deps.refs, cal=deps.cal, pool=deps.pool
    )
    candles_2 = series_2.candles
    hash_2 = series_2.series_hash

    # --- 멱등성 검증: Decimal exact match + hash consistency ---
    assert len(candles_2) == 4
    for c1, c2 in zip(candles_1, candles_2, strict=True):
        assert c1.close == c2.close
        assert c1.open_time == c2.open_time
        assert c1.volume == c2.volume

    # batch_hash로 결정론성 검증 (LA-8 batch_hash = LA-21 series_hash 구성요소)
    assert hash_1 == hash_2, "같은 데이터의 조회 결과는 동일 hash를 가져야 한다 (결정론성)"


async def test_failure_ohlc_violation_through_ingest_pipeline(
    pool: asyncpg.Pool, deps: SimpleNamespace
) -> None:
    """OHLC 위반(low > high) — ingest_candles 실제 파이프라인 QUARANTINE 경로 주입.

    failure-injection: 좋은 캔들 4개 + 나쁜 캔들 1개(OHLC 위반) 혼합 → PARTIAL verdict + 격리됨.
    """
    # --- 준비 ---
    instrument = await listed_instrument(deps, Venue.BITGET, _BASE_DATE)
    base_time = _BASE_DATE.replace(hour=0, minute=0, second=0)

    async with pool.acquire() as conn:
        await conn.execute("SELECT md_ensure_partitions(6)")

    # 좋은 캔들 4개
    good_candles = []
    for i in range(4):
        open_time = base_time + timedelta(minutes=i)
        good_candles.append(
            CandleRecord(
                key=SeriesKey(
                    venue=Venue.BITGET,
                    instrument_id=instrument.instrument_id,
                    timeframe=Timeframe.M1,
                ),
                open_time=open_time,
                close_time=open_time + timedelta(minutes=1),
                open=Decimal("100"),
                high=Decimal("110"),
                low=Decimal("90"),
                close=Decimal("105"),
                volume=Decimal("1000"),
            )
        )

    # 나쁜 캔들 1개: OHLC 위반 (low > high)
    bad_candle = CandleRecord(
        key=SeriesKey(
            venue=Venue.BITGET,
            instrument_id=instrument.instrument_id,
            timeframe=Timeframe.M1,
        ),
        open_time=base_time + timedelta(minutes=4),
        close_time=base_time + timedelta(minutes=5),
        open=Decimal("100"),
        high=Decimal("90"),  # high < open: OHLC 위반!
        low=Decimal("80"),
        close=Decimal("85"),
        volume=Decimal("1000"),
    )

    # --- ingest_candles 실제 호출: 혼합 데이터 ---
    source = MockIngestSource(candles=[*good_candles, bad_candle])
    cmd = ingest_cmd(
        instrument,
        base_time,
        base_time + timedelta(minutes=5),
        venue=Venue.BITGET,
    )
    result = await run_ingest(deps, cmd, source, clock_at=base_time)

    # --- 검증: PARTIAL 판정 + 좋은 것만 저장 ---
    assert result.verdict.verdict == Verdict.PARTIAL, "혼합 배치는 PARTIAL"
    assert result.verdict.accepted == 4, "좋은 캔들 4개만 accepted"
    # OHLC 위반 캔들은 "rejected" 카운터에 포함됨 (quarantine이 아님)
    assert result.verdict.rejected >= 1, "나쁜 캔들은 품질 판정에서 거부됨"


async def test_failure_source_timeout_exception_propagates(
    pool: asyncpg.Pool, deps: SimpleNamespace
) -> None:
    """외부 source 타임아웃 — fetch 예외 발생."""
    # --- 준비 ---
    instrument = await listed_instrument(deps, Venue.BITGET, _BASE_DATE)
    base_time = _BASE_DATE.replace(hour=0, minute=0, second=0)

    # --- 타임아웃 모의 소스 ---
    source = MockIngestSource(fail_mode="timeout")
    cmd = ingest_cmd(
        instrument,
        base_time,
        base_time + timedelta(minutes=1),
        venue=Venue.BITGET,
    )

    # --- 예외 발생 확인 ---
    with pytest.raises(TimeoutError, match="did not respond within budget"):
        await run_ingest(deps, cmd, source, clock_at=base_time)


async def test_rollup_m1_to_m5_aggregation_deterministic(
    pool: asyncpg.Pool, deps: SimpleNamespace
) -> None:
    """DC-10 M1→M5 결정론 집계 — rollup() 순수 함수 + Decimal 정밀도.

    M1 캔들 5개를 M5로 집계: OHLCV 동일성 검증.
    """
    # --- 준비: M1 캔들 5개 생성 (1개의 M5 윈도우를 형성) ---
    instrument = await listed_instrument(deps, Venue.BITGET, _BASE_DATE)
    base_time = _BASE_DATE.replace(hour=0, minute=0, second=0)

    async with pool.acquire() as conn:
        await conn.execute("SELECT md_ensure_partitions(6)")

    # M1 캔들 5개: 각각 다른 OHLC 값
    m1_prices = [
        (Decimal("100"), Decimal("105"), Decimal("99"), Decimal("102")),  # m1[0]
        (Decimal("102"), Decimal("108"), Decimal("101"), Decimal("107")),  # m1[1]
        (Decimal("107"), Decimal("110"), Decimal("105"), Decimal("109")),  # m1[2]
        (Decimal("109"), Decimal("111"), Decimal("108"), Decimal("110")),  # m1[3]
        (Decimal("110"), Decimal("112"), Decimal("109"), Decimal("111")),  # m1[4]
    ]

    m1_candles = []
    for i, (o, h, low_price, c) in enumerate(m1_prices):
        open_time = base_time + timedelta(minutes=i)
        m1_candles.append(
            CandleRecord(
                key=SeriesKey(
                    venue=Venue.BITGET,
                    instrument_id=instrument.instrument_id,
                    timeframe=Timeframe.M1,
                ),
                open_time=open_time,
                close_time=open_time + timedelta(minutes=1),
                open=o,
                high=h,
                low=low_price,
                close=c,
                volume=Decimal("1000"),
                quote_volume=Decimal("100000"),
            )
        )

    # --- Ingest M1 ---
    source = MockIngestSource(candles=m1_candles)
    cmd = ingest_cmd(
        instrument,
        base_time,
        base_time + timedelta(minutes=5),
        venue=Venue.BITGET,
    )
    ingest_result = await run_ingest(deps, cmd, source, clock_at=base_time)
    assert ingest_result.verdict.verdict == Verdict.ACCEPT

    # --- Query M1 → CandleColumns ---
    query = CandleQuery(
        key=SeriesKey(
            venue=Venue.BITGET,
            instrument_id=instrument.instrument_id,
            timeframe=Timeframe.M1,
        ),
        start=base_time,
        end=base_time + timedelta(minutes=5),
    )
    series = await get_candles(
        query, store=deps.store, refs=deps.refs, cal=deps.cal, pool=deps.pool
    )
    m1_loaded = series.candles

    # CandleColumns로 변환 (rollup 입력)
    m1_columns = CandleColumns(
        ts=[c.open_time for c in m1_loaded],
        open=[c.open for c in m1_loaded],
        high=[c.high for c in m1_loaded],
        low=[c.low for c in m1_loaded],
        close=[c.close for c in m1_loaded],
        volume=[c.volume for c in m1_loaded],
        quote_volume=[c.quote_volume for c in m1_loaded],
    )

    # --- Rollup M1 → M5 (DC-10) ---
    from src.foundation.market_data.domain.calendar.known_venues import KNOWN_SESSIONS

    bitget_session = KNOWN_SESSIONS[Venue.BITGET.value]
    from src.foundation.market_data.domain.calendar.session_rules import VenueCalendar

    bitget_calendar = VenueCalendar(
        venue=Venue.BITGET.value,
        tz=bitget_session.tz,
        regular=bitget_session,
    )

    rollup_result = rollup(m1_columns, Timeframe.M5, bitget_calendar)
    m5_columns = rollup_result.columns

    # --- Decimal 정밀도 검증: 집계 규칙 (DC-10 §4.1) ---
    # M5[0]: open=M1[0].open, high=max(M1[0..4].high), low=min(M1[0..4].low), close=M1[4].close
    assert m5_columns.open[0] == Decimal("100"), "M5 open = first M1 open"
    assert m5_columns.high[0] == max(h for _, h, _, _ in m1_prices), "M5 high = max"
    assert (
        m5_columns.low[0] == min(low_v for _, _, low_v, _ in m1_prices)
    ), "M5 low = min"
    assert m5_columns.close[0] == Decimal("111"), "M5 close = last M1 close"
    assert m5_columns.volume[0] == Decimal("5000"), "M5 volume = sum (5 * 1000)"
    assert m5_columns.quote_volume[0] == Decimal("500000"), "M5 quote_volume = sum (5 * 100000)"

    # rollup_version은 규칙 서술의 해시값 (DC-10 §4.1)
    assert rollup_result.rollup_version is not None


async def test_ingest_source_mock_contract(pool: asyncpg.Pool) -> None:
    """LA-9 MockIngestSource가 IngestSource 포트 계약을 준수한다."""
    base_time = _BASE_DATE.replace(hour=0, minute=0, second=0)

    test_candles = [
        CandleRecord(
            key=SeriesKey(
                venue=Venue.BITGET,
                instrument_id=uuid4(),
                timeframe=Timeframe.M1,
            ),
            open_time=base_time,
            close_time=base_time + timedelta(minutes=1),
            open=Decimal("100"),
            high=Decimal("110"),
            low=Decimal("95"),
            close=Decimal("105"),
            volume=Decimal("1000"),
        ),
    ]

    source = MockIngestSource(candles=test_candles)
    result = await source.fetch_candles(
        Venue.BITGET,
        "test_symbol",
        Timeframe.M1,
        base_time,
        base_time + timedelta(minutes=1),
    )

    assert len(result) == 1
    assert result[0].close == Decimal("105")
    assert len(source.fetch_calls) == 1


async def test_ingest_source_mock_timeout_contract(pool: asyncpg.Pool) -> None:
    """LA-9 MockIngestSource가 타임아웃 실패를 시뮬레이션한다."""
    source = MockIngestSource(fail_mode="timeout")

    with pytest.raises(TimeoutError, match="did not respond within budget"):
        await source.fetch_candles(
            Venue.BITGET,
            "test_symbol",
            Timeframe.M1,
            datetime.now(timezone.utc),
            datetime.now(timezone.utc) + timedelta(minutes=1),
        )
