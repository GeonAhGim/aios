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
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import asyncpg
import pytest

from src.core.db.conditional_write import ConcurrencyConflictError
from src.data.models.base import AssetClass
from src.foundation.market_data.adapters.postgres_coverage_repository import (
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
        "created_at": datetime.now(UTC),
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
