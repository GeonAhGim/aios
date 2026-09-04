"""DC-8 통합 테스트 — coverage_spans/entitlements DB 제약(실 DB, TEST_DATABASE_URL).

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§4.1, §6(커버리지 밖 구간은 DATA_COVERAGE_MISSING, 0/NaN 채움 금지), §9.2 DC-8.

`tests/foundation/integration/market_data/test_instruments_schema.py`(DC-4)와
동일 패턴 — 애플리케이션 계층을 거치지 않고 raw SQL로 직접 INSERT해
`coverage_spans`의 `EXCLUDE USING gist`가 같은 (instrument_id, venue,
timeframe, quality) 축 안의 겹침을 DB 레벨에서 거부함을 단언한다.
`entitlements`는 (tenant_id, subject_id, venue, timeframe, feed_type)
UNIQUE 제약과, 스코프 쿼리(tenant_id/subject_id로 필터링)가 다른 주체의
행을 반환하지 않음을 단언한다.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import asyncpg
import pytest


def _asyncpg_dsn() -> str:
    return os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=8)
    yield p
    await p.close()


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


async def _insert_span(
    pool: asyncpg.Pool,
    *,
    instrument_id: str,
    venue: str = "BITGET",
    timeframe: str = "1m",
    quality: str = "PROVISIONAL",
    start_at: datetime,
    end_at: datetime,
) -> None:
    await pool.execute(
        """
        INSERT INTO coverage_spans (instrument_id, venue, timeframe, quality, start_at, end_at)
        VALUES ($1, $2, $3, $4, $5, $6)
        """,
        instrument_id, venue, timeframe, quality, start_at, end_at,
    )


async def test_coverage_spans_rejects_overlapping_period_same_axis(pool):
    instrument_id = _fake_ulid()
    await _insert_instrument(pool, instrument_id)
    t0 = datetime.now(timezone.utc) - timedelta(days=10)

    await _insert_span(
        pool, instrument_id=instrument_id, start_at=t0, end_at=t0 + timedelta(days=5)
    )

    with pytest.raises(asyncpg.exceptions.ExclusionViolationError):
        await _insert_span(
            pool,
            instrument_id=instrument_id,
            start_at=t0 + timedelta(days=2),
            end_at=t0 + timedelta(days=8),
        )


async def test_coverage_spans_allows_adjacent_non_overlapping_periods(pool):
    """`[t0,t1)`와 `[t1,t2)`는 경계만 맞닿아 있고 겹치지 않으므로 성공해야
    한다 — DC-6 `merge_spans`가 병합 대상으로 삼는 바로 그 상황."""
    instrument_id = _fake_ulid()
    await _insert_instrument(pool, instrument_id)
    t0 = datetime.now(timezone.utc) - timedelta(days=10)
    t1 = t0 + timedelta(days=5)

    await _insert_span(pool, instrument_id=instrument_id, start_at=t0, end_at=t1)
    await _insert_span(
        pool, instrument_id=instrument_id, start_at=t1, end_at=t1 + timedelta(days=5)
    )

    rows = await pool.fetch(
        "SELECT start_at FROM coverage_spans WHERE instrument_id = $1 ORDER BY start_at",
        instrument_id,
    )
    assert len(rows) == 2


async def test_coverage_spans_allows_overlap_across_different_quality(pool):
    """EXCLUDE 축은 quality를 포함한다 — 같은 기간이라도 quality가 다르면
    독립적인 선언(예: RAW 벤더와 VALIDATED 벤더의 각자 커버리지 선언)이라
    겹쳐도 막히지 않아야 한다(과도한 배제 방지 회귀)."""
    instrument_id = _fake_ulid()
    await _insert_instrument(pool, instrument_id)
    t0 = datetime.now(timezone.utc) - timedelta(days=10)

    await _insert_span(
        pool, instrument_id=instrument_id, quality="PROVISIONAL",
        start_at=t0, end_at=t0 + timedelta(days=5),
    )
    await _insert_span(
        pool, instrument_id=instrument_id, quality="VALIDATED",
        start_at=t0, end_at=t0 + timedelta(days=5),
    )

    rows = await pool.fetch(
        "SELECT quality FROM coverage_spans WHERE instrument_id = $1", instrument_id
    )
    assert len(rows) == 2


async def test_coverage_spans_rejects_start_after_end(pool):
    instrument_id = _fake_ulid()
    await _insert_instrument(pool, instrument_id)
    t0 = datetime.now(timezone.utc)

    with pytest.raises(asyncpg.exceptions.CheckViolationError):
        await _insert_span(
            pool, instrument_id=instrument_id, start_at=t0, end_at=t0 - timedelta(days=1)
        )


async def test_entitlements_unique_constraint_rejects_duplicate_grant(pool):
    tenant_id = uuid.uuid4()
    subject_id = uuid.uuid4()

    async def _grant() -> None:
        await pool.execute(
            "INSERT INTO entitlements (tenant_id, subject_id, venue, timeframe, feed_type) "
            "VALUES ($1, $2, 'BITGET', '1m', 'REALTIME')",
            tenant_id, subject_id,
        )

    await _grant()
    with pytest.raises(asyncpg.exceptions.UniqueViolationError):
        await _grant()


async def test_entitlements_scoped_query_never_returns_other_subjects_rows(pool):
    """negative: subject_id로 스코프한 조회는 다른 subject의 entitlement
    행을 절대 반환하면 안 된다 — 남의 entitlements를 반환하지 않는다는
    DoD를 스토리지 조회 패턴 자체로 단언한다."""
    tenant_id = uuid.uuid4()
    subject_a = uuid.uuid4()
    subject_b = uuid.uuid4()

    await pool.execute(
        "INSERT INTO entitlements (tenant_id, subject_id, venue, timeframe, feed_type) "
        "VALUES ($1, $2, 'BITGET', '1m', 'REALTIME')",
        tenant_id, subject_a,
    )
    await pool.execute(
        "INSERT INTO entitlements (tenant_id, subject_id, venue, timeframe, feed_type) "
        "VALUES ($1, $2, 'BITGET', '1m', 'DELAYED')",
        tenant_id, subject_b,
    )

    rows = await pool.fetch(
        "SELECT subject_id, feed_type FROM entitlements WHERE tenant_id = $1 AND subject_id = $2",
        tenant_id, subject_a,
    )
    assert len(rows) == 1
    assert rows[0]["subject_id"] == subject_a
    assert rows[0]["feed_type"] == "REALTIME"


async def test_entitlements_rejects_expiry_before_grant(pool):
    tenant_id = uuid.uuid4()
    subject_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    with pytest.raises(asyncpg.exceptions.CheckViolationError):
        await pool.execute(
            "INSERT INTO entitlements "
            "(tenant_id, subject_id, venue, timeframe, feed_type, granted_at, expires_at) "
            "VALUES ($1, $2, 'BITGET', '1m', 'REALTIME', $3, $4)",
            tenant_id, subject_id, now, now - timedelta(days=1),
        )
