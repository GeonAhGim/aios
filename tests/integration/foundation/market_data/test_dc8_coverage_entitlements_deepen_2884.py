"""DC-8 coverage_spans/entitlements + Postgres 어댑터 2종 — DEEPEN(task-2884,
DEPTH_DC_RD 소급감사) D1 -> D3 증빙.

`test_coverage_spans_schema.py`/`test_dc8_instrument_repository.py`/
`test_coverage_repository.py`는 EXCLUDE·UNIQUE·CHECK 제약과 어댑터
정상계·주요 음성계를 순차 실행으로만 증명했다(D1). 부족분: (1) 아직
안 건드린 CHECK/FK 경계값 실패주입, (2) 어댑터 경유 수치 성능 예산,
(3) 위반이 트랜잭션 전체를 롤백함(부분 커밋 없음)의 게이트 적색 재현,
(4) 여러 커넥션(다중 인스턴스/워커 시뮬레이션)이 동시에 경합할 때도
EXCLUDE/UNIQUE/조건부 UPDATE가 TOCTOU 없이 정확히 하나만 통과시키는
D3 동시성 증명. `src/db/migrations/versions/9049e2b6b0b7_*.py`와
`postgres_coverage_repository.py`/`postgres_instrument_repository.py`는
무수정 유지 — 새 기능 없음, 깊이만 올린다.
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import asyncpg
import pytest

from src.core.db.conditional_write import ConcurrencyConflictError
from src.data.models.base import AssetClass
from src.foundation.market_data.adapters.postgres_coverage_repository import (
    CoverageSpanOverlapError,
    PostgresCoverageRepository,
)
from src.foundation.market_data.adapters.postgres_instrument_repository import (
    PostgresInstrumentRepository,
)
from src.foundation.market_data.contracts.v1 import Timeframe, Venue
from src.foundation.market_data.contracts.v2.instruments import Instrument, InstrumentLifecycle
from src.foundation.market_data.ports.coverage_repository import CoverageQuality, CoverageSpan


def _asyncpg_dsn() -> str:
    return os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=2, max_size=32)
    yield p
    await p.close()


@pytest.fixture
def coverage_repo(pool):
    return PostgresCoverageRepository(pool)


@pytest.fixture
def instrument_repo(pool):
    return PostgresInstrumentRepository(pool)


def _fake_ulid() -> str:
    return "0" + uuid.uuid4().hex[:25].upper()


def _instrument(**overrides) -> Instrument:
    fields = {
        "instrument_id": _fake_ulid(),
        "asset_class": AssetClass.CRYPTO,
        "base": "BTC",
        "quote": "USDT",
        "isin": None,
        "figi": None,
        "tick_size": Decimal("0.01"),
        "lot_size": Decimal("0.0001"),
        "calendar_id": "24x7",
        "lifecycle_state": InstrumentLifecycle.ACTIVE,
        "created_at": datetime.now(timezone.utc),
    }
    fields.update(overrides)
    return Instrument(**fields)


async def _insert_instrument(conn: asyncpg.Connection | asyncpg.Pool, instrument_id: str) -> None:
    await conn.execute(
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


async def _grant_entitlement(
    conn: asyncpg.Connection | asyncpg.Pool,
    *,
    tenant_id: uuid.UUID,
    subject_id: uuid.UUID,
    venue: str = "BITGET",
    timeframe: str = "1m",
    feed_type: str = "REALTIME",
) -> None:
    await conn.execute(
        "INSERT INTO entitlements (tenant_id, subject_id, venue, timeframe, feed_type) "
        "VALUES ($1, $2, $3, $4, $5)",
        tenant_id,
        subject_id,
        venue,
        timeframe,
        feed_type,
    )


# ---- 실패주입(D2) — 기존 테스트가 안 건드린 CHECK/FK 경계값 ----


async def test_coverage_spans_rejects_unknown_venue(pool):
    """venue CHECK IN (...) — 심볼 마스터가 모르는 venue 문자열은 거부된다."""
    instrument_id = _fake_ulid()
    await _insert_instrument(pool, instrument_id)
    t0 = datetime.now(timezone.utc)

    with pytest.raises(asyncpg.exceptions.CheckViolationError):
        await pool.execute(
            "INSERT INTO coverage_spans "
            "(instrument_id, venue, timeframe, quality, start_at, end_at) "
            "VALUES ($1, 'NOT_A_REAL_VENUE', '1m', 'PROVISIONAL', $2, $3)",
            instrument_id,
            t0,
            t0 + timedelta(days=1),
        )


async def test_coverage_spans_rejects_unknown_instrument_id_fk(coverage_repo, pool):
    """instrument_id FK — 존재하지 않는 instrument를 참조하는 span은
    거부된다(기존 테스트는 항상 미리 instrument를 만들어 이 경로를 안 탔다)."""
    t0 = datetime.now(timezone.utc)
    with pytest.raises(asyncpg.exceptions.ForeignKeyViolationError):
        async with pool.acquire() as conn, conn.transaction():
            await coverage_repo.upsert_span(
                conn, _span(instrument_id=_fake_ulid(), start=t0, end=t0 + timedelta(days=1))
            )


async def test_coverage_spans_rejects_one_microsecond_overlap_boundary(coverage_repo, pool):
    """정확히 1마이크로초 겹치는 구간도 어댑터를 통해 거부된다 — half-open
    경계(`[start, end)`)가 소수점 이하에서도 정확함을 증명한다."""
    instrument_id = _fake_ulid()
    await _insert_instrument(pool, instrument_id)
    t0 = datetime.now(timezone.utc) - timedelta(days=10)
    boundary = t0 + timedelta(days=5)

    async with pool.acquire() as conn, conn.transaction():
        await coverage_repo.upsert_span(
            conn, _span(instrument_id=instrument_id, start=t0, end=boundary)
        )

    with pytest.raises(CoverageSpanOverlapError):
        async with pool.acquire() as conn, conn.transaction():
            await coverage_repo.upsert_span(
                conn,
                _span(
                    instrument_id=instrument_id,
                    start=boundary - timedelta(microseconds=1),
                    end=boundary + timedelta(days=1),
                ),
            )


async def test_coverage_spans_allows_touching_boundary_exactly(coverage_repo, pool):
    """직전 end_at과 정확히 같은 시각에 시작하는 구간은 겹치지 않는다
    (half-open 경계 — 위 마이크로초 테스트의 반대쪽 경계)."""
    instrument_id = _fake_ulid()
    await _insert_instrument(pool, instrument_id)
    t0 = datetime.now(timezone.utc) - timedelta(days=10)
    boundary = t0 + timedelta(days=5)

    async with pool.acquire() as conn, conn.transaction():
        await coverage_repo.upsert_span(
            conn, _span(instrument_id=instrument_id, start=t0, end=boundary)
        )
    async with pool.acquire() as conn, conn.transaction():
        await coverage_repo.upsert_span(
            conn,
            _span(instrument_id=instrument_id, start=boundary, end=boundary + timedelta(days=1)),
        )


async def test_entitlements_rejects_negative_delayed_seconds(pool):
    """CHECK (delayed_seconds >= 0) — 음수 지연초는 거부된다."""
    with pytest.raises(asyncpg.exceptions.CheckViolationError):
        await pool.execute(
            "INSERT INTO entitlements "
            "(tenant_id, subject_id, venue, timeframe, feed_type, delayed_seconds) "
            "VALUES ($1, $2, 'BITGET', '1m', 'DELAYED', -1)",
            uuid.uuid4(),
            uuid.uuid4(),
        )


async def test_entitlements_rejects_unknown_feed_type(pool):
    """feed_type CHECK IN ('REALTIME','DELAYED') — 그 외 값은 거부된다."""
    with pytest.raises(asyncpg.exceptions.CheckViolationError):
        await pool.execute(
            "INSERT INTO entitlements (tenant_id, subject_id, venue, timeframe, feed_type) "
            "VALUES ($1, $2, 'BITGET', '1m', 'BATCH')",
            uuid.uuid4(),
            uuid.uuid4(),
        )


async def test_entitlements_rejects_expiry_equal_to_granted_at(pool):
    """CHECK (expires_at IS NULL OR expires_at > granted_at) — 등호 경계값이
    거부됨을 직접 증명한다(기존 테스트는 이전 시각만 시험했다)."""
    now = datetime.now(timezone.utc)
    with pytest.raises(asyncpg.exceptions.CheckViolationError):
        await pool.execute(
            "INSERT INTO entitlements "
            "(tenant_id, subject_id, venue, timeframe, feed_type, granted_at, expires_at) "
            "VALUES ($1, $2, 'BITGET', '1m', 'REALTIME', $3, $4)",
            uuid.uuid4(),
            uuid.uuid4(),
            now,
            now,
        )


# ---- 성능 단언(D2) — 어댑터 경유 ----


@pytest.mark.perf
async def test_bulk_upsert_span_meets_latency_budget(coverage_repo, pool):
    """`EXCLUDE USING gist`는 삽입마다 GiST 인덱스를 스캔해 겹침을 검사한다
    — 인덱스가 안 타면 O(n) 열화로 느려진다. 500개 서로 겹치지 않는 span을
    어댑터(`upsert_span`)로 연속 삽입해 절대시간 예산 내임을 단언한다."""
    instrument_id = _fake_ulid()
    await _insert_instrument(pool, instrument_id)
    t0 = datetime.now(timezone.utc) - timedelta(days=2000)
    n = 500
    budget_sec = 15.0

    start = time.perf_counter()
    for i in range(n):
        async with pool.acquire() as conn, conn.transaction():
            await coverage_repo.upsert_span(
                conn,
                _span(
                    instrument_id=instrument_id,
                    start=t0 + timedelta(days=i),
                    end=t0 + timedelta(days=i + 1),
                ),
            )
    elapsed = time.perf_counter() - start
    print(
        f"[DC-8 coverage_spans] {n} upsert_span in {elapsed:.3f}s "
        f"({elapsed / n * 1e3:.2f} ms/insert, budget<{budget_sec}s)"
    )
    assert elapsed < budget_sec, (
        f"coverage_spans {n}건 upsert_span이 예산({budget_sec}s)을 넘었습니다"
        f"({elapsed:.3f}s) — GiST 인덱스가 안 타는지 확인하세요."
    )

    async with pool.acquire() as conn, conn.transaction():
        spans = await coverage_repo.list_spans(conn, instrument_id, Timeframe.M1)
    assert len(spans) == n


# ---- 게이트 적색 재현(D2) — 위반이 트랜잭션 전체를 롤백함(부분 커밋 없음) ----


async def test_gate_red_coverage_span_overlap_rolls_back_whole_transaction(coverage_repo, pool):
    """같은 트랜잭션 안에서 (a) 합법적인 새 instrument 삽입 (b) 그 뒤 겹치는
    span upsert(위반)를 순서대로 실행하면, 트랜잭션 전체가 롤백돼 (a)도
    커밋되지 않아야 한다 — 게이트 적색이 원자적 전체 실패임을 증명한다."""
    instrument_a = _fake_ulid()
    await _insert_instrument(pool, instrument_a)
    t0 = datetime.now(timezone.utc) - timedelta(days=10)
    async with pool.acquire() as conn, conn.transaction():
        await coverage_repo.upsert_span(
            conn, _span(instrument_id=instrument_a, start=t0, end=t0 + timedelta(days=5))
        )

    instrument_b = _fake_ulid()
    async with pool.acquire() as conn:
        with pytest.raises(CoverageSpanOverlapError):
            async with conn.transaction():
                await _insert_instrument(conn, instrument_b)
                await coverage_repo.upsert_span(
                    conn,
                    _span(
                        instrument_id=instrument_a,
                        start=t0 + timedelta(days=1),
                        end=t0 + timedelta(days=6),
                    ),
                )

    row = await pool.fetchrow(
        "SELECT instrument_id FROM instruments WHERE instrument_id = $1", instrument_b
    )
    assert row is None, "게이트 위반 트랜잭션의 앞선 INSERT가 커밋되어 남았습니다"


async def test_gate_red_entitlements_unique_violation_rolls_back_transaction(pool):
    """같은 트랜잭션 안에서 (a) 새 entitlement 행(다른 subject) 삽입 (b) 그
    뒤 UNIQUE 위반 중복 삽입을 실행하면, 트랜잭션 전체가 롤백돼 (a)도
    커밋되지 않아야 한다."""
    tenant_id = uuid.uuid4()
    subject_dup = uuid.uuid4()
    subject_sibling = uuid.uuid4()
    await _grant_entitlement(pool, tenant_id=tenant_id, subject_id=subject_dup)

    async with pool.acquire() as conn:
        with pytest.raises(asyncpg.exceptions.UniqueViolationError):
            async with conn.transaction():
                await _grant_entitlement(conn, tenant_id=tenant_id, subject_id=subject_sibling)
                await _grant_entitlement(conn, tenant_id=tenant_id, subject_id=subject_dup)

    row = await pool.fetchrow(
        "SELECT 1 FROM entitlements WHERE tenant_id = $1 AND subject_id = $2",
        tenant_id,
        subject_sibling,
    )
    assert row is None, "게이트 위반 트랜잭션의 앞선 entitlement INSERT가 커밋되어 남았습니다"


# ---- 동시 다중 인스턴스/워커(D3) — TOCTOU 경합에서도 제약이 지킨다 ----


async def test_concurrent_overlapping_upsert_span_exactly_one_winner(coverage_repo, pool):
    """서로 다른 커넥션(다중 워커/인스턴스 시뮬레이션) 10개가 같은
    (instrument_id, venue, timeframe, quality) 축에서 서로 겹치는 구간에
    동시에 `upsert_span`을 시도한다. 앱 레벨 check-then-insert였다면
    TOCTOU 경합으로 여럿이 통과할 수 있지만, EXCLUDE USING gist는 DB가
    직렬화하므로 정확히 1개만 성공해야 한다."""
    instrument_id = _fake_ulid()
    await _insert_instrument(pool, instrument_id)
    t0 = datetime.now(timezone.utc) - timedelta(days=1)
    n = 10

    async def _attempt(i: int) -> bool:
        try:
            async with pool.acquire() as conn, conn.transaction():
                await coverage_repo.upsert_span(
                    conn,
                    _span(
                        instrument_id=instrument_id,
                        start=t0 + timedelta(microseconds=i),
                        end=t0 + timedelta(days=1),
                    ),
                )
            return True
        except CoverageSpanOverlapError:
            return False

    results = await asyncio.gather(*(_attempt(i) for i in range(n)))

    assert sum(results) == 1, (
        f"동시 upsert_span {n}건 중 성공이 {sum(results)}건입니다 — EXCLUDE가 "
        "경합 상황에서 정확히 하나만 통과시키지 못했습니다"
    )
    async with pool.acquire() as conn, conn.transaction():
        spans = await coverage_repo.list_spans(conn, instrument_id, Timeframe.M1)
    assert len(spans) == 1


async def test_concurrent_non_overlapping_upsert_span_all_succeed(coverage_repo, pool):
    """겹치지 않는 구간이면 동시에 upsert해도 전부 성공해야 한다 —
    EXCLUDE가 과도하게 넓게 직렬화(모든 동시 쓰기를 막음)하지 않는다는
    회귀 방지."""
    instrument_id = _fake_ulid()
    await _insert_instrument(pool, instrument_id)
    t0 = datetime.now(timezone.utc) - timedelta(days=100)
    n = 20

    async def _attempt(i: int) -> None:
        async with pool.acquire() as conn, conn.transaction():
            await coverage_repo.upsert_span(
                conn,
                _span(
                    instrument_id=instrument_id,
                    start=t0 + timedelta(days=i * 2),
                    end=t0 + timedelta(days=i * 2 + 1),
                ),
            )

    await asyncio.gather(*(_attempt(i) for i in range(n)))

    async with pool.acquire() as conn, conn.transaction():
        spans = await coverage_repo.list_spans(conn, instrument_id, Timeframe.M1)
    assert len(spans) == n


async def test_concurrent_update_lifecycle_state_exactly_one_winner(instrument_repo, pool):
    """105번 표준(조건부 UPDATE)이 실제 동시 경합 하에서도 지켜지는지 —
    기존 `test_update_lifecycle_state_rejects_stale_expected_state`는 순차
    실행으로 선행 변경을 주입했을 뿐이다(D1). 여기서는 8개 워커가 진짜
    동시에 같은 `expected_state=ACTIVE`로 서로 다른 목표 상태 전이를
    시도한다 — WHERE 조건의 원자적 UPDATE 덕에 정확히 1개만 성공해야
    한다."""
    instrument = _instrument()
    async with pool.acquire() as conn, conn.transaction():
        await instrument_repo.create(conn, instrument)

    targets = [
        InstrumentLifecycle.HALTED,
        InstrumentLifecycle.DELISTED,
        InstrumentLifecycle.HALTED,
        InstrumentLifecycle.DELISTED,
        InstrumentLifecycle.HALTED,
        InstrumentLifecycle.DELISTED,
        InstrumentLifecycle.HALTED,
        InstrumentLifecycle.DELISTED,
    ]

    async def _attempt(target: InstrumentLifecycle) -> bool:
        try:
            async with pool.acquire() as conn, conn.transaction():
                await instrument_repo.update_lifecycle_state(
                    conn,
                    instrument.instrument_id,
                    expected_state=InstrumentLifecycle.ACTIVE,
                    state=target,
                )
            return True
        except ConcurrencyConflictError:
            return False

    results = await asyncio.gather(*(_attempt(t) for t in targets))

    assert sum(results) == 1, (
        f"동시 update_lifecycle_state {len(targets)}건 중 성공이 {sum(results)}건입니다 "
        "— 조건부 UPDATE가 경합에서 정확히 하나만 통과시키지 못했습니다"
    )
    async with pool.acquire() as conn, conn.transaction():
        final = await instrument_repo.get(conn, instrument.instrument_id)
    assert final is not None
    assert final.lifecycle_state != InstrumentLifecycle.ACTIVE


async def test_concurrent_entitlements_duplicate_grant_exactly_one_winner(pool):
    """8개 워커가 동시에 같은 (tenant_id, subject_id, venue, timeframe,
    feed_type)로 entitlement 부여를 시도한다 — UNIQUE 제약이 경합에서도
    정확히 1개만 통과시켜야 한다(이중 라이선스 부여 방지, entitlements는
    decision상 어댑터가 없어 raw SQL로 증명)."""
    tenant_id = uuid.uuid4()
    subject_id = uuid.uuid4()
    n = 8

    async def _attempt() -> bool:
        try:
            async with pool.acquire() as conn:
                await _grant_entitlement(conn, tenant_id=tenant_id, subject_id=subject_id)
            return True
        except asyncpg.exceptions.UniqueViolationError:
            return False

    results = await asyncio.gather(*(_attempt() for _ in range(n)))

    assert sum(results) == 1, (
        f"동시 entitlement 부여 {n}건 중 성공이 {sum(results)}건입니다 — UNIQUE가 "
        "경합 상황에서 정확히 하나만 통과시키지 못했습니다"
    )
    rows = await pool.fetch(
        "SELECT count(*) AS n FROM entitlements WHERE tenant_id = $1 AND subject_id = $2",
        tenant_id,
        subject_id,
    )
    assert rows[0]["n"] == 1
