"""`application/get_coverage.get_coverage` 통합테스트 — 실 DB(TEST_DATABASE_URL).

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md#§9.2
DC-18(backend half, task-2195). DoD: (a) 병합 구간과 quality_grade가 정확,
(b) 커버리지가 전혀 없는 구간은 빈 리스트(예외 아님), (c) 이용권 없는(=
"다른 테넌트") venue 조회도 빈 리스트, (d) 마이그레이션 신설 없음(이
파일은 기존 DC-4/DC-8 테이블만 씀).
"""

from __future__ import annotations

import math
import time
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

import asyncpg
import pytest

from src.data.models.base import AssetClass
from src.foundation.market_data.adapters.postgres_coverage_repository import (
    PostgresCoverageRepository,
)
from src.foundation.market_data.adapters.postgres_instrument_repository import (
    PostgresInstrumentRepository,
)
from src.foundation.market_data.adapters.postgres_tenant_venues import PostgresTenantVenueSource
from src.foundation.market_data.application.get_coverage import get_coverage
from src.foundation.market_data.contracts.v1 import Timeframe, Venue
from src.foundation.market_data.contracts.v2.coverage import QualityGrade
from src.foundation.market_data.contracts.v2.instruments import Instrument, InstrumentLifecycle
from src.foundation.market_data.ports.coverage_repository import CoverageQuality, CoverageSpan


def _fake_ulid() -> str:
    return "0" + uuid.uuid4().hex[:25].upper()


def _instrument(instrument_id: str) -> Instrument:
    return Instrument(
        instrument_id=instrument_id,
        asset_class=AssetClass.CRYPTO,
        base="BTC",
        quote="USDT",
        isin=None,
        figi=None,
        tick_size=Decimal("0.01"),
        lot_size=Decimal("0.0001"),
        calendar_id="24x7",
        lifecycle_state=InstrumentLifecycle.ACTIVE,
        created_at=datetime.now(timezone.utc),
    )


def _span(
    *,
    instrument_id: str,
    start: datetime,
    end: datetime,
    venue: Venue = Venue.BITGET,
    quality: CoverageQuality = CoverageQuality.PROVISIONAL,
    timeframe: Timeframe = Timeframe.M1,
) -> CoverageSpan:
    return CoverageSpan(
        instrument_id=instrument_id,
        venue=venue,
        timeframe=timeframe,
        quality=quality,
        start=start,
        end=end,
    )


async def _grant_entitlement(pool: asyncpg.Pool, *, tenant_id: UUID, venue: Venue) -> None:
    await pool.execute(
        "INSERT INTO entitlements (tenant_id, subject_id, venue, timeframe, feed_type) "
        "VALUES ($1, $2, $3, $4, 'DELAYED')",
        tenant_id,
        uuid.uuid4(),
        venue.value,
        Timeframe.M1.value,
    )


@pytest.fixture(autouse=True)
async def _cleanup_upbit_coverage_rows(pool):
    """`test_get_coverage_ignores_spans_from_other_venues`가 심는
    `Venue.UPBIT` `coverage_spans` 행은 정리하지 않으면
    `test_downgrade_then_upgrade_round_trip`(migration round trip)이
    4b19195124bb를 downgrade할 때 되살리는 구 CHECK(`_OLD_VENUES`에 'UPBIT'
    없음)를 위반해 공유 TEST_DATABASE_URL 세션 전체를 깨뜨린다 —
    test_fa0c_account_scope.py의 `_cleanup_portfolio_test_accounts`와
    동일한 위생 규칙."""
    yield
    await pool.execute("DELETE FROM coverage_spans WHERE venue = 'UPBIT'")


@pytest.fixture
def coverage_repo(pool):
    return PostgresCoverageRepository(pool)


@pytest.fixture
def instrument_repo(pool):
    return PostgresInstrumentRepository(pool)


@pytest.fixture
def venue_registry(pool):
    return PostgresTenantVenueSource(pool)


async def test_get_coverage_merges_adjacent_spans_and_keeps_quality_axes_separate(
    pool, coverage_repo, instrument_repo, venue_registry
):
    tenant_id = uuid.uuid4()
    instrument_id = _fake_ulid()
    t0 = datetime.now(timezone.utc) - timedelta(days=10)

    async with pool.acquire() as conn, conn.transaction():
        await instrument_repo.create(conn, _instrument(instrument_id))
    await _grant_entitlement(pool, tenant_id=tenant_id, venue=Venue.BITGET)
    async with pool.acquire() as conn, conn.transaction():
        await coverage_repo.upsert_span(
            conn, _span(instrument_id=instrument_id, start=t0, end=t0 + timedelta(days=5))
        )
    async with pool.acquire() as conn, conn.transaction():
        # 경계가 맞닿은 두 번째 PROVISIONAL span — 첫 span과 병합돼야 한다.
        await coverage_repo.upsert_span(
            conn,
            _span(
                instrument_id=instrument_id,
                start=t0 + timedelta(days=5),
                end=t0 + timedelta(days=8),
            ),
        )
    async with pool.acquire() as conn, conn.transaction():
        # 다른 quality 축 — 겹쳐도 병합 대상이 아니라 별개로 남아야 한다.
        await coverage_repo.upsert_span(
            conn,
            _span(
                instrument_id=instrument_id,
                start=t0 + timedelta(days=1),
                end=t0 + timedelta(days=3),
                quality=CoverageQuality.VALIDATED,
            ),
        )

    async with pool.acquire() as conn, conn.transaction():
        spans = await get_coverage(
            conn,
            tenant_id=tenant_id,
            instrument_id=instrument_id,
            venue=Venue.BITGET,
            timeframe=Timeframe.M1,
            coverage_repo=coverage_repo,
            instrument_repo=instrument_repo,
            venue_registry=venue_registry,
        )

    by_grade = {s.quality_grade: s for s in spans}
    assert len(spans) == 2
    assert by_grade[QualityGrade.RAW].start_at == t0
    assert by_grade[QualityGrade.RAW].end_at == t0 + timedelta(days=8)
    assert by_grade[QualityGrade.VALIDATED].start_at == t0 + timedelta(days=1)
    assert all(s.asset_class == AssetClass.CRYPTO for s in spans)


async def test_get_coverage_returns_empty_list_for_uncovered_axis(
    pool, instrument_repo, coverage_repo, venue_registry
):
    """(b) 커버리지 선언이 전혀 없는 구간은 예외가 아니라 빈 리스트다."""
    tenant_id = uuid.uuid4()
    instrument_id = _fake_ulid()
    async with pool.acquire() as conn, conn.transaction():
        await instrument_repo.create(conn, _instrument(instrument_id))
    await _grant_entitlement(pool, tenant_id=tenant_id, venue=Venue.BITGET)

    async with pool.acquire() as conn, conn.transaction():
        spans = await get_coverage(
            conn,
            tenant_id=tenant_id,
            instrument_id=instrument_id,
            venue=Venue.BITGET,
            timeframe=Timeframe.M1,
            coverage_repo=coverage_repo,
            instrument_repo=instrument_repo,
            venue_registry=venue_registry,
        )
    assert spans == []


async def test_get_coverage_returns_empty_list_for_tenant_without_entitlement(
    pool, instrument_repo, coverage_repo, venue_registry
):
    """negative (c): 커버리지는 있지만 이 테넌트는 그 venue 이용권이 없다
    (= 다른 테넌트의 instrument_id 조회 동형) — 0행, 예외/500 아님."""
    other_tenant_id = uuid.uuid4()
    instrument_id = _fake_ulid()
    t0 = datetime.now(timezone.utc) - timedelta(days=10)

    async with pool.acquire() as conn, conn.transaction():
        await instrument_repo.create(conn, _instrument(instrument_id))
    async with pool.acquire() as conn, conn.transaction():
        await coverage_repo.upsert_span(
            conn, _span(instrument_id=instrument_id, start=t0, end=t0 + timedelta(days=5))
        )
    # other_tenant_id never granted an entitlement for BITGET.

    async with pool.acquire() as conn, conn.transaction():
        spans = await get_coverage(
            conn,
            tenant_id=other_tenant_id,
            instrument_id=instrument_id,
            venue=Venue.BITGET,
            timeframe=Timeframe.M1,
            coverage_repo=coverage_repo,
            instrument_repo=instrument_repo,
            venue_registry=venue_registry,
        )
    assert spans == []


async def test_get_coverage_ignores_spans_from_other_venues(
    pool, instrument_repo, coverage_repo, venue_registry
):
    """`CoverageRepository.list_spans`는 venue로 필터링하지 않으므로(포트
    시그니처에 venue가 없다) `get_coverage`가 직접 걸러야 한다."""
    tenant_id = uuid.uuid4()
    instrument_id = _fake_ulid()
    t0 = datetime.now(timezone.utc) - timedelta(days=10)

    async with pool.acquire() as conn, conn.transaction():
        await instrument_repo.create(conn, _instrument(instrument_id))
    await _grant_entitlement(pool, tenant_id=tenant_id, venue=Venue.BITGET)
    async with pool.acquire() as conn, conn.transaction():
        await coverage_repo.upsert_span(
            conn,
            _span(
                instrument_id=instrument_id,
                start=t0,
                end=t0 + timedelta(days=5),
                venue=Venue.UPBIT,
            ),
        )

    async with pool.acquire() as conn, conn.transaction():
        spans = await get_coverage(
            conn,
            tenant_id=tenant_id,
            instrument_id=instrument_id,
            venue=Venue.BITGET,
            timeframe=Timeframe.M1,
            coverage_repo=coverage_repo,
            instrument_repo=instrument_repo,
            venue_registry=venue_registry,
        )
    assert spans == []


async def test_get_coverage_ignores_spans_declared_for_other_timeframe(
    pool, instrument_repo, coverage_repo, venue_registry
):
    """negative (추가): 커버리지가 선언돼 있지만 요청한 timeframe과 다른
    축(5m)뿐이면 빈 리스트다 — `PostgresCoverageRepository.list_spans`의
    `WHERE timeframe = $2` 필터가 느슨해지는 회귀(예: 다른 timeframe 데이터가
    섞여 나옴)를 잡는다. 기존 negative 2건(무이용권 테넌트/타venue)과는
    다른 축(timeframe)을 겨냥한다."""
    tenant_id = uuid.uuid4()
    instrument_id = _fake_ulid()
    t0 = datetime.now(timezone.utc) - timedelta(days=10)

    async with pool.acquire() as conn, conn.transaction():
        await instrument_repo.create(conn, _instrument(instrument_id))
    await _grant_entitlement(pool, tenant_id=tenant_id, venue=Venue.BITGET)
    async with pool.acquire() as conn, conn.transaction():
        await coverage_repo.upsert_span(
            conn,
            _span(
                instrument_id=instrument_id,
                start=t0,
                end=t0 + timedelta(days=5),
                timeframe=Timeframe.M5,
            ),
        )

    async with pool.acquire() as conn, conn.transaction():
        spans = await get_coverage(
            conn,
            tenant_id=tenant_id,
            instrument_id=instrument_id,
            venue=Venue.BITGET,
            timeframe=Timeframe.M1,
            coverage_repo=coverage_repo,
            instrument_repo=instrument_repo,
            venue_registry=venue_registry,
        )
    assert spans == []


async def test_get_coverage_instrument_lookup_failure_propagates_fail_closed(
    pool, instrument_repo, coverage_repo, venue_registry, monkeypatch
):
    """실패주입+게이트재현 — 지금까지의 negative는 전부 "정상 입력이지만
    이용권/커버리지가 없음"(fail-closed 빈 리스트)이었을 뿐, instrument_repo
    자체가 인프라 장애로 죽는 경우는 한 번도 흉내내지 않았다.
    `get_coverage`의 `instrument is None` 분기(동시 삭제 방어용,
    application/get_coverage.py 모듈 docstring 참고)는 "없음"과 "조회
    실패"를 구분해야 한다 — instrument_repo.get이 예외로 죽으면 그 분기가
    삼켜 빈 리스트로 위장하지 않고 예외를 그대로 전파해야 한다(부분 검증
    후 조용한 통과 없음). 이 테스트는 향후 누군가 instrument_repo.get
    호출을 try/except로 감싸 실패를 빈 리스트로 바꾸는 회귀가 들어오면
    즉시 적색이 된다(게이트재현)."""
    tenant_id = uuid.uuid4()
    instrument_id = _fake_ulid()
    t0 = datetime.now(timezone.utc) - timedelta(days=10)

    async with pool.acquire() as conn, conn.transaction():
        await instrument_repo.create(conn, _instrument(instrument_id))
    await _grant_entitlement(pool, tenant_id=tenant_id, venue=Venue.BITGET)
    async with pool.acquire() as conn, conn.transaction():
        await coverage_repo.upsert_span(
            conn, _span(instrument_id=instrument_id, start=t0, end=t0 + timedelta(days=5))
        )

    async def _flaky_get(conn: asyncpg.Connection, instrument_id: str):
        raise asyncpg.PostgresConnectionError("simulated instrument_repo adapter outage")

    monkeypatch.setattr(instrument_repo, "get", _flaky_get)

    async with pool.acquire() as conn, conn.transaction():
        with pytest.raises(asyncpg.PostgresConnectionError):
            await get_coverage(
                conn,
                tenant_id=tenant_id,
                instrument_id=instrument_id,
                venue=Venue.BITGET,
                timeframe=Timeframe.M1,
                coverage_repo=coverage_repo,
                instrument_repo=instrument_repo,
                venue_registry=venue_registry,
            )


async def test_get_coverage_latency_stays_within_normalized_ceiling_as_span_count_grows(
    pool, instrument_repo, coverage_repo, venue_registry
):
    """수치 성능 단언 — 공유 TEST_DATABASE_URL의 절대 지연 변동성 때문에
    절대 ms 임계 대신 baseline(1 span) 대비 정규화한 상한만 게이트로 쓴다
    (task-3033 FA-6 test_get_statement_portfolio_scope.py와 동일 교훈).
    `get_coverage`는 DB 라운드트립을 정확히 3회(venue_registry.
    registered_venues / coverage_repo.list_spans / instrument_repo.get)만
    해야 하므로, span 개수가 20배(1 -> 20) 늘어도 지연이 거의 늘지 않아야
    한다 — span당 1회씩 DB를 왕복하거나 merge_spans가 이차식으로 느려지는
    회귀가 생기면 이 상한을 넘는다."""
    tenant_id = uuid.uuid4()
    baseline_id = _fake_ulid()
    scaled_id = _fake_ulid()
    t0 = datetime.now(timezone.utc) - timedelta(days=400)

    async with pool.acquire() as conn, conn.transaction():
        await instrument_repo.create(conn, _instrument(baseline_id))
        await instrument_repo.create(conn, _instrument(scaled_id))
    await _grant_entitlement(pool, tenant_id=tenant_id, venue=Venue.BITGET)

    async with pool.acquire() as conn, conn.transaction():
        await coverage_repo.upsert_span(
            conn, _span(instrument_id=baseline_id, start=t0, end=t0 + timedelta(days=1))
        )

    for i in range(20):
        gap_start = t0 + timedelta(days=i * 2)
        async with pool.acquire() as conn, conn.transaction():
            await coverage_repo.upsert_span(
                conn,
                _span(
                    instrument_id=scaled_id,
                    start=gap_start,
                    end=gap_start + timedelta(days=1),
                ),
            )

    async def _call(instrument_id: str) -> float:
        start = time.perf_counter()
        async with pool.acquire() as conn, conn.transaction():
            await get_coverage(
                conn,
                tenant_id=tenant_id,
                instrument_id=instrument_id,
                venue=Venue.BITGET,
                timeframe=Timeframe.M1,
                coverage_repo=coverage_repo,
                instrument_repo=instrument_repo,
                venue_registry=venue_registry,
            )
        return time.perf_counter() - start

    baseline_samples = sorted([await _call(baseline_id) for _ in range(10)])
    scaled_samples = sorted([await _call(scaled_id) for _ in range(10)])
    baseline_p95 = baseline_samples[math.ceil(0.95 * len(baseline_samples)) - 1]
    scaled_p95 = scaled_samples[math.ceil(0.95 * len(scaled_samples)) - 1]

    ceiling = baseline_p95 * 5 + 0.05
    assert scaled_p95 <= ceiling, (
        f"get_coverage span=20 p95 지연 {scaled_p95:.4f}s가 baseline(span=1) 대비 "
        f"정규화 상한 {ceiling:.4f}s(baseline {baseline_p95:.4f}s)를 초과했습니다 -- "
        "span당 DB 왕복이 늘거나 merge_spans가 이차식으로 느려지는 회귀 의심"
    )
