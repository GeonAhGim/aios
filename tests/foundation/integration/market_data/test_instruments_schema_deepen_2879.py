"""DC-4 instruments/venue_listings — DEEPEN(task-2879, DEPTH_DC_RD 소급감사)
D1 -> D3 증빙.

`test_instruments_schema.py`는 순차 실행 기준 EXCLUDE/트리거의 정상계·주요
음성계만 증명했다(D1) — 부족분: 실패주입 범위(CHECK/FK/UNIQUE 경계값),
수치 성능 단언, 게이트 적색이 트랜잭션을 전부 롤백함(부분 커밋 없음)의
증명, 그리고 동시 다중 커넥션(D3 — 여러 "인스턴스"가 동시에 같은 DB에
쓰는 상황) 경합에서도 EXCLUDE/트리거가 깨지지 않는다는 증명이다.
앱 레벨(check-then-insert) 검사는 TOCTOU 경합에 취약하지만, DB 제약은
그 경합 자체를 없앤다는 것이 이 파일의 핵심 주장이다. 새 기능 없음,
깊이만 올린다.
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from datetime import datetime, timedelta, timezone

import asyncpg
import pytest


def _asyncpg_dsn() -> str:
    return os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=2, max_size=32)
    yield p
    await p.close()


def _fake_ulid() -> str:
    return "0" + uuid.uuid4().hex[:25].upper()


def _venue_symbol() -> str:
    return f"T{uuid.uuid4().hex[:10].upper()}USDT"


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


async def _insert_listing(
    conn: asyncpg.Connection | asyncpg.Pool,
    *,
    instrument_id: str,
    venue: str,
    venue_symbol: str,
    listed_at: datetime,
    delisted_at: datetime | None,
) -> None:
    await conn.execute(
        """
        INSERT INTO venue_listings (
            instrument_id, venue, venue_symbol, listed_at, delisted_at, is_primary
        ) VALUES ($1, $2, $3, $4, $5, TRUE)
        """,
        instrument_id,
        venue,
        venue_symbol,
        listed_at,
        delisted_at,
    )


# ---- 실패주입(D2) — CHECK/FK/UNIQUE 경계값, 정상 테스트가 놓친 제약 ----


async def test_venue_listings_rejects_delisted_at_equal_to_listed_at(pool):
    """CHECK (delisted_at IS NULL OR delisted_at > listed_at) — 등호 경계값이
    거부됨을 직접 증명한다(기존 테스트는 이 CHECK를 전혀 건드리지 않았다)."""
    instrument_id = _fake_ulid()
    await _insert_instrument(pool, instrument_id)
    t0 = datetime.now(timezone.utc) - timedelta(days=1)

    with pytest.raises(asyncpg.exceptions.CheckViolationError):
        await _insert_listing(
            pool,
            instrument_id=instrument_id,
            venue="BITGET",
            venue_symbol=_venue_symbol(),
            listed_at=t0,
            delisted_at=t0,
        )


async def test_venue_listings_rejects_delisted_at_before_listed_at(pool):
    instrument_id = _fake_ulid()
    await _insert_instrument(pool, instrument_id)
    t0 = datetime.now(timezone.utc) - timedelta(days=1)

    with pytest.raises(asyncpg.exceptions.CheckViolationError):
        await _insert_listing(
            pool,
            instrument_id=instrument_id,
            venue="BITGET",
            venue_symbol=_venue_symbol(),
            listed_at=t0,
            delisted_at=t0 - timedelta(microseconds=1),
        )


async def test_venue_listings_rejects_unknown_venue(pool):
    """venue CHECK IN (...) — 심볼 마스터가 모르는 venue 문자열은 거부된다."""
    instrument_id = _fake_ulid()
    await _insert_instrument(pool, instrument_id)
    t0 = datetime.now(timezone.utc)

    with pytest.raises(asyncpg.exceptions.CheckViolationError):
        await _insert_listing(
            pool,
            instrument_id=instrument_id,
            venue="NOT_A_REAL_VENUE",
            venue_symbol=_venue_symbol(),
            listed_at=t0,
            delisted_at=None,
        )


async def test_venue_listings_rejects_unknown_instrument_id_fk(pool):
    """instrument_id FK — 존재하지 않는 instrument를 참조하는 listing은
    거부된다(기존 테스트는 항상 미리 instrument를 만들어 이 경로를 안 탔다)."""
    with pytest.raises(asyncpg.exceptions.ForeignKeyViolationError):
        await _insert_listing(
            pool,
            instrument_id=_fake_ulid(),
            venue="BITGET",
            venue_symbol=_venue_symbol(),
            listed_at=datetime.now(timezone.utc),
            delisted_at=None,
        )


async def test_venue_listings_rejects_exact_duplicate_start_same_symbol(pool):
    """UNIQUE (venue, venue_symbol, listed_at) — EXCLUDE보다 먼저 걸리는
    정확한 시작시각 중복도 거부돼야 한다(두 제약이 독립적으로 동작함)."""
    instrument_a = _fake_ulid()
    instrument_b = _fake_ulid()
    await _insert_instrument(pool, instrument_a)
    await _insert_instrument(pool, instrument_b)
    symbol = _venue_symbol()
    t0 = datetime.now(timezone.utc) - timedelta(days=10)

    await _insert_listing(
        pool,
        instrument_id=instrument_a,
        venue="BITGET",
        venue_symbol=symbol,
        listed_at=t0,
        delisted_at=t0 + timedelta(days=1),
    )
    with pytest.raises(
        (asyncpg.exceptions.UniqueViolationError, asyncpg.exceptions.ExclusionViolationError)
    ):
        await _insert_listing(
            pool,
            instrument_id=instrument_b,
            venue="BITGET",
            venue_symbol=symbol,
            listed_at=t0,
            delisted_at=t0 + timedelta(days=2),
        )


async def test_venue_listings_rejects_one_microsecond_overlap(pool):
    """정확히 1마이크로초 겹치는 구간도 거부된다 — half-open 경계
    (`[listed_at, delisted_at)`)가 소수점 이하에서도 정확함을 증명한다."""
    instrument_a = _fake_ulid()
    instrument_b = _fake_ulid()
    await _insert_instrument(pool, instrument_a)
    await _insert_instrument(pool, instrument_b)
    symbol = _venue_symbol()
    t0 = datetime.now(timezone.utc) - timedelta(days=10)
    boundary = t0 + timedelta(days=5)

    await _insert_listing(
        pool,
        instrument_id=instrument_a,
        venue="BITGET",
        venue_symbol=symbol,
        listed_at=t0,
        delisted_at=boundary,
    )
    with pytest.raises(asyncpg.exceptions.ExclusionViolationError):
        await _insert_listing(
            pool,
            instrument_id=instrument_b,
            venue="BITGET",
            venue_symbol=symbol,
            listed_at=boundary - timedelta(microseconds=1),
            delisted_at=None,
        )


async def test_venue_listings_allows_touching_boundary_exactly(pool):
    """직전 delisted_at과 정확히 같은 시각에 시작하는 구간은 겹치지 않는다
    (half-open 경계 — 위 마이크로초 테스트의 반대쪽 경계)."""
    instrument_a = _fake_ulid()
    instrument_b = _fake_ulid()
    await _insert_instrument(pool, instrument_a)
    await _insert_instrument(pool, instrument_b)
    symbol = _venue_symbol()
    t0 = datetime.now(timezone.utc) - timedelta(days=10)
    boundary = t0 + timedelta(days=5)

    await _insert_listing(
        pool,
        instrument_id=instrument_a,
        venue="BITGET",
        venue_symbol=symbol,
        listed_at=t0,
        delisted_at=boundary,
    )
    await _insert_listing(
        pool,
        instrument_id=instrument_b,
        venue="BITGET",
        venue_symbol=symbol,
        listed_at=boundary,
        delisted_at=None,
    )


# ---- 성능 단언(D2) ----


@pytest.mark.perf
async def test_bulk_non_overlapping_inserts_meet_latency_budget(pool):
    """`EXCLUDE USING gist`는 삽입마다 겹침 검사를 위해 GiST 인덱스를
    스캔한다 — 인덱스가 없거나 퇴화하면 O(n) 순차비교로 느려진다. 500개
    서로 겹치지 않는 listing을 연속 삽입해 절대시간 예산 내임을 단언한다
    (실측 로컬 <2s, 예산은 느린 CI 대비 넉넉히 잡음)."""
    instrument_id = _fake_ulid()
    await _insert_instrument(pool, instrument_id)
    symbol = _venue_symbol()
    t0 = datetime.now(timezone.utc) - timedelta(days=2000)
    n = 500
    budget_sec = 15.0

    start = time.perf_counter()
    for i in range(n):
        await _insert_listing(
            pool,
            instrument_id=instrument_id,
            venue="BITGET",
            venue_symbol=symbol,
            listed_at=t0 + timedelta(days=i),
            delisted_at=t0 + timedelta(days=i + 1),
        )
    elapsed = time.perf_counter() - start
    print(
        f"[DC-4 venue_listings] {n} inserts in {elapsed:.3f}s "
        f"({elapsed / n * 1e3:.2f} ms/insert, budget<{budget_sec}s)"
    )
    assert elapsed < budget_sec, (
        f"venue_listings {n}건 삽입이 예산({budget_sec}s)을 넘었습니다"
        f"({elapsed:.3f}s) — GiST 인덱스가 안 타는지 확인하세요."
    )

    rows = await pool.fetch(
        "SELECT count(*) AS n FROM venue_listings WHERE instrument_id = $1", instrument_id
    )
    assert rows[0]["n"] == n


# ---- 게이트 적색 재현(D2) — 위반이 트랜잭션 전체를 롤백함(부분 커밋 없음) ----


async def test_gate_red_exclusion_violation_rolls_back_whole_transaction(pool):
    """같은 트랜잭션 안에서 (a) 합법적인 새 instrument 삽입 (b) 그 뒤 겹치는
    listing 삽입(위반)을 순서대로 실행하면, 트랜잭션 전체가 롤백돼 (a)도
    커밋되지 않아야 한다 — 게이트 적색이 "이 statement만" 취소가 아니라
    원자적 전체 실패임을 증명한다(부분 상태 오염 방지)."""
    instrument_a = _fake_ulid()
    await _insert_instrument(pool, instrument_a)
    symbol = _venue_symbol()
    t0 = datetime.now(timezone.utc) - timedelta(days=10)
    await _insert_listing(
        pool,
        instrument_id=instrument_a,
        venue="BITGET",
        venue_symbol=symbol,
        listed_at=t0,
        delisted_at=None,
    )

    instrument_b = _fake_ulid()
    async with pool.acquire() as conn:
        with pytest.raises(asyncpg.exceptions.ExclusionViolationError):
            async with conn.transaction():
                await _insert_instrument(conn, instrument_b)
                await _insert_listing(
                    conn,
                    instrument_id=instrument_b,
                    venue="BITGET",
                    venue_symbol=symbol,
                    listed_at=t0 + timedelta(days=1),
                    delisted_at=None,
                )

    row = await pool.fetchrow(
        "SELECT instrument_id FROM instruments WHERE instrument_id = $1", instrument_b
    )
    assert row is None, "게이트 위반 트랜잭션의 앞선 INSERT가 커밋되어 남았습니다"


async def test_gate_red_instrument_id_update_rejects_multi_row_statement_atomically(pool):
    """한 UPDATE 문이 여러 행을 건드리고 그중 하나만 instrument_id를
    바꾸려 해도, 트리거가 그 statement 전체를 거부해 나머지 행의
    lifecycle_state 변경도 적용되지 않아야 한다(원자성)."""
    keep_id = _fake_ulid()
    victim_id = _fake_ulid()
    await _insert_instrument(pool, keep_id)
    await _insert_instrument(pool, victim_id)

    with pytest.raises(asyncpg.exceptions.CheckViolationError):
        await pool.execute(
            """
            UPDATE instruments SET
                lifecycle_state = 'HALTED',
                instrument_id = CASE WHEN instrument_id = $1 THEN $2 ELSE instrument_id END
            WHERE instrument_id IN ($1, $3)
            """,
            victim_id,
            _fake_ulid(),
            keep_id,
        )

    row = await pool.fetchrow(
        "SELECT lifecycle_state FROM instruments WHERE instrument_id = $1", keep_id
    )
    assert row["lifecycle_state"] == "ACTIVE", (
        "victim 행의 id 변경 시도가 거부됐음에도 무관한 keep 행의 UPDATE가 "
        "부분 적용됐습니다 — statement 원자성 위반"
    )


# ---- 동시 다중 인스턴스/워커(D3) — TOCTOU 경합에서도 DB 제약이 지킨다 ----


async def test_concurrent_overlapping_inserts_exactly_one_winner(pool):
    """서로 다른 커넥션(다중 워커/인스턴스 시뮬레이션) 10개가 같은
    (venue, venue_symbol)에서 서로 겹치는(그러나 `listed_at`은 각기 다른 —
    UNIQUE(venue, venue_symbol, listed_at)이 아니라 EXCLUDE 자체를 시험하기
    위함) 기간에 동시에 INSERT를 시도한다. 앱 레벨 check-then-insert였다면
    전부 "안 겹침"을 보고 통과할 수 있는 TOCTOU 경합이지만, EXCLUDE USING
    gist는 DB가 직렬화하므로 정확히 1개만 성공해야 한다."""
    symbol = _venue_symbol()
    t0 = datetime.now(timezone.utc) - timedelta(days=1)
    n = 10
    instrument_ids = [_fake_ulid() for _ in range(n)]
    for iid in instrument_ids:
        await _insert_instrument(pool, iid)

    async def _attempt(i: int, instrument_id: str) -> bool:
        try:
            async with pool.acquire() as conn:
                await _insert_listing(
                    conn,
                    instrument_id=instrument_id,
                    venue="BITGET",
                    venue_symbol=symbol,
                    listed_at=t0 + timedelta(microseconds=i),
                    delisted_at=t0 + timedelta(days=1),
                )
            return True
        except asyncpg.exceptions.ExclusionViolationError:
            return False

    results = await asyncio.gather(*(_attempt(i, iid) for i, iid in enumerate(instrument_ids)))

    assert sum(results) == 1, (
        f"동시 삽입 {n}건 중 성공이 {sum(results)}건입니다 — EXCLUDE가 "
        "경합 상황에서 정확히 하나만 통과시키지 못했습니다"
    )
    rows = await pool.fetch(
        "SELECT count(*) AS n FROM venue_listings WHERE venue_symbol = $1", symbol
    )
    assert rows[0]["n"] == 1


async def test_concurrent_non_overlapping_inserts_all_succeed(pool):
    """겹치지 않는 기간이면 동시에 삽입해도 전부 성공해야 한다 — EXCLUDE가
    과도하게 넓게 직렬화(모든 동시 쓰기를 막음)하지 않는다는 회귀 방지."""
    symbol = _venue_symbol()
    t0 = datetime.now(timezone.utc) - timedelta(days=100)
    n = 20
    instrument_ids = [_fake_ulid() for _ in range(n)]
    for iid in instrument_ids:
        await _insert_instrument(pool, iid)

    async def _attempt(i: int, instrument_id: str) -> None:
        async with pool.acquire() as conn:
            await _insert_listing(
                conn,
                instrument_id=instrument_id,
                venue="BITGET",
                venue_symbol=symbol,
                listed_at=t0 + timedelta(days=i * 2),
                delisted_at=t0 + timedelta(days=i * 2 + 1),
            )

    await asyncio.gather(*(_attempt(i, iid) for i, iid in enumerate(instrument_ids)))

    rows = await pool.fetch(
        "SELECT count(*) AS n FROM venue_listings WHERE venue_symbol = $1", symbol
    )
    assert rows[0]["n"] == n


async def test_concurrent_instrument_id_update_attempts_all_rejected(pool):
    """여러 워커가 동시에 같은 instrument의 instrument_id를 바꾸려 시도해도
    전부 거부되고, 원본 id가 그대로 살아있어야 한다(트리거가 동시성 하에서도
    구멍 없음)."""
    instrument_id = _fake_ulid()
    await _insert_instrument(pool, instrument_id)

    async def _attempt() -> bool:
        try:
            async with pool.acquire() as conn:
                await conn.execute(
                    "UPDATE instruments SET instrument_id = $1 WHERE instrument_id = $2",
                    _fake_ulid(),
                    instrument_id,
                )
            return True
        except asyncpg.exceptions.CheckViolationError:
            return False

    results = await asyncio.gather(*(_attempt() for _ in range(8)))

    assert all(result is False for result in results), (
        "동시 instrument_id UPDATE 시도 중 일부가 트리거를 통과했습니다"
    )
    row = await pool.fetchrow(
        "SELECT instrument_id FROM instruments WHERE instrument_id = $1", instrument_id
    )
    assert row is not None, "instrument_id가 여전히 원본이어야 합니다"
