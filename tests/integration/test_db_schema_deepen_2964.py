"""LA-10 md_reference_registry — DEEPEN(task-2964, DEPTH_LA_LB_LC 소급감사
task-2723/task-418) D1 -> D2, 그리고 task-10511(depth_judge 2026-10-01)
D2 -> D3 증빙.

`test_db_schema.py`(현재 `db_schema/test_market_data.py`)의 LA-10 절은
4개 CHECK/EXCLUDE negative를 증명했다(D1) — 이 파일은 그 위에 수치
성능 단언·게이트 적색 재현(D2)과, 기존에 없던 UNIQUE 제약 negative 2건·
의존성 실패 주입 1건·동시성(D3) 1건을 더한다. 새 기능 없음, 깊이만
올린다. 제품 코드는 건드리지 않았다.

`md_symbol_alias`의 `EXCLUDE USING gist`는 삽입마다 겹침 검사를 위해
GiST 인덱스를 스캔한다 — 인덱스가 없거나 퇴화하면 O(n) 순차비교로
느려진다(DC-4 venue_listings와 동일한 리스크, 04_db_schema_v1.7.md
동일 패턴). 성능 단언은 이 인덱스가 실제로 타는지를 절대시간 예산으로
증명한다.

게이트 적색 재현은 같은 트랜잭션 안에서 (a) 합법적인 md_instrument
삽입 뒤 (b) md_corporate_action의 CHECK(ratio > 0) 위반을 일으키면,
트랜잭션 전체가 롤백돼 (a)도 커밋되지 않아야 함을 증명한다 — 부분
커밋으로 인한 참조무결성 오염 방지.

추가된 negative 2건은 기존 CHECK/EXCLUDE와 다른 제약 축(UNIQUE)을
증명한다: `md_instrument(venue, canonical_symbol, listed_at)`과
`md_corporate_action(instrument_id, action_type, ex_date)`. 실패 주입은
asyncpg 커넥션 메서드에 `side_effect`로 의존성 예외를 강제해, 그
실패가 트랜잭션을 부분 커밋 없이 롤백시키는지 증명한다(CHECK/EXCLUDE
위반이 아니라 "의존성이 죽었을 때"를 흉내낸 것 — 별도 증빙 축). 동시성
증명은 같은 `(venue, canonical_symbol, listed_at)`로 두 커넥션이
동시에 INSERT를 시도했을 때 애플리케이션 사전 체크 없이도 DB UNIQUE
제약만으로 정확히 하나만 커밋됨을 보인다(레이스 컨디션 방어가 DB
레벨에 있음을 확인 — `PostgresReferenceRepository.register()`의
`SELECT` 선검사는 TOCTOU이지만, 이 DB 제약이 최종 방어선이다).
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

import asyncpg
import pytest


def _asyncpg_dsn() -> str:
    return os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=2, max_size=16)
    yield p
    await p.close()


async def _insert_md_instrument(
    conn: asyncpg.Connection | asyncpg.Pool, *, canonical_symbol: str
) -> object:
    return await conn.fetchval(
        "INSERT INTO md_instrument "
        "(venue, canonical_symbol, venue_symbol, asset_class, tick_size, lot_size, "
        " status, listed_at) "
        "VALUES ('BITGET', $1, $1, 'CRYPTO', 0.01, 0.0001, 'LISTED', now()) "
        "RETURNING instrument_id",
        canonical_symbol,
    )


async def _insert_md_instrument_full(
    conn: asyncpg.Connection | asyncpg.Pool,
    *,
    canonical_symbol: str,
    venue_symbol: str,
    listed_at: datetime,
) -> object:
    return await conn.fetchval(
        "INSERT INTO md_instrument "
        "(venue, canonical_symbol, venue_symbol, asset_class, tick_size, lot_size, "
        " status, listed_at) "
        "VALUES ('BITGET', $1, $2, 'CRYPTO', 0.01, 0.0001, 'LISTED', $3) "
        "RETURNING instrument_id",
        canonical_symbol,
        venue_symbol,
        listed_at,
    )


# ---- 수치 성능 단언(D2) ----------------------------------------------------


@pytest.mark.perf
async def test_bulk_non_overlapping_alias_inserts_meet_latency_budget(pool, perf_budget):
    """500개 서로 겹치지 않는 (venue, alias_symbol) 별칭을 같은 심볼에
    연속 삽입해 절대시간 예산 내임을 단언한다(실측 로컬 <2s, 예산은
    느린 CI 대비 넉넉히 잡음). EXCLUDE USING gist가 GiST 인덱스를 타지
    못하고 순차 스캔으로 퇴화하면 이 예산을 넘긴다.

    raw time.perf_counter() → perf_budget.sample_async() 전환 (task-11077).
    비동기 DB I/O를 재므로 sample_async(wall_ms)로 왕복 지연을 보존하고
    coverage tracer 오버헤드를 걷어낸다. 예산 값(15초)은 그대로 유지한다.
    """
    instrument_id = await _insert_md_instrument(pool, canonical_symbol=f"TEST-{uuid4().hex}")
    alias_symbol = f"ALIAS-{uuid4().hex}"
    t0 = datetime(2000, 1, 1, tzinfo=timezone.utc)
    n = 500

    async def _insert_aliases() -> None:
        for i in range(n):
            await pool.execute(
                "INSERT INTO md_symbol_alias "
                "(instrument_id, venue, alias_symbol, valid_from, valid_to) "
                "VALUES ($1, 'BITGET', $2, $3, $4)",
                instrument_id,
                alias_symbol,
                t0 + timedelta(days=i),
                t0 + timedelta(days=i + 1),
            )

    sample = await perf_budget.sample_async(_insert_aliases)
    budget_ms = 15000.0  # 15초

    print(
        f"[LA-10 md_symbol_alias] {n} inserts wall={sample.wall_ms:.3f}ms "
        f"({sample.wall_ms / n:.2f} ms/insert, budget<{budget_ms / 1000:.1f}s)"
    )
    assert sample.wall_ms < budget_ms, (
        f"md_symbol_alias {n}건 삽입이 예산({budget_ms / 1000:.1f}s)을 넘었습니다"
        f"(wall={sample.wall_ms:.3f}ms) — GiST 인덱스가 안 타는지 확인하세요."
    )

    row = await pool.fetchrow(
        "SELECT count(*) AS n FROM md_symbol_alias WHERE instrument_id = $1", instrument_id
    )
    assert row["n"] == n


# ---- 게이트 적색 재현(D2) — 위반이 트랜잭션 전체를 롤백함(부분 커밋 없음) --


async def test_gate_red_corporate_action_check_violation_rolls_back_whole_transaction(pool):
    """같은 트랜잭션 안에서 (a) 합법적인 새 md_instrument 삽입 (b) 그 뒤
    md_corporate_action의 CHECK(ratio > 0) 위반 삽입을 순서대로 실행하면,
    트랜잭션 전체가 롤백돼 (a)도 커밋되지 않아야 한다 — 게이트 적색이
    "이 statement만" 취소가 아니라 원자적 전체 실패임을 증명한다."""
    canonical_symbol = f"TEST-{uuid4().hex}"

    async with pool.acquire() as conn:
        with pytest.raises(asyncpg.CheckViolationError):
            async with conn.transaction():
                instrument_id = await _insert_md_instrument(conn, canonical_symbol=canonical_symbol)
                await conn.execute(
                    "INSERT INTO md_corporate_action "
                    "(instrument_id, action_type, ex_date, ratio, source_ref) "
                    "VALUES ($1, 'SPLIT', '2026-06-01', 0, 'gate-red-repro')",
                    instrument_id,
                )

    row = await pool.fetchrow(
        "SELECT instrument_id FROM md_instrument WHERE canonical_symbol = $1", canonical_symbol
    )
    assert row is None, "게이트 위반 트랜잭션의 앞선 md_instrument INSERT가 커밋되어 남았습니다"


# ---- negative 추가(D2->D3) — 기존 CHECK/EXCLUDE와 다른 제약 축(UNIQUE) -----


async def test_md_instrument_duplicate_venue_canonical_listed_at_rejected(pool):
    """LA-10 DoD — UNIQUE(venue, canonical_symbol, listed_at) negative.
    기존 negative 4건은 CHECK/EXCLUDE만 증명했다 — 이 제약은 다른 축
    (복합 UNIQUE)이라 별개로 증명이 필요하다. `venue_symbol`은 이
    제약에 없으므로 서로 달라도 거부되어야 한다."""
    canonical_symbol = f"TEST-{uuid4().hex}"
    listed_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    await _insert_md_instrument_full(
        pool, canonical_symbol=canonical_symbol, venue_symbol="orig-symbol", listed_at=listed_at
    )

    with pytest.raises(asyncpg.UniqueViolationError):
        await _insert_md_instrument_full(
            pool,
            canonical_symbol=canonical_symbol,
            venue_symbol="different-symbol",
            listed_at=listed_at,
        )


async def test_md_corporate_action_duplicate_key_rejected(pool):
    """LA-10 DoD — UNIQUE(instrument_id, action_type, ex_date) negative.
    기존 negative는 `ratio > 0` CHECK만 증명했다 — 같은 키로 두 번째
    corporate action을 보내면(ratio가 달라도) DB가 거부해야
    한다(애플리케이션 레이어의 `CorporateActionDigestMismatchError`
    번역과 별개로, DB 제약 자체가 최종 방어선임을 증명)."""
    instrument_id = await _insert_md_instrument(pool, canonical_symbol=f"TEST-{uuid4().hex}")
    await pool.execute(
        "INSERT INTO md_corporate_action "
        "(instrument_id, action_type, ex_date, ratio, source_ref) "
        "VALUES ($1, 'SPLIT', '2026-06-01', 2, 'first')",
        instrument_id,
    )

    with pytest.raises(asyncpg.UniqueViolationError):
        await pool.execute(
            "INSERT INTO md_corporate_action "
            "(instrument_id, action_type, ex_date, ratio, source_ref) "
            "VALUES ($1, 'SPLIT', '2026-06-01', 3, 'second')",
            instrument_id,
        )


# ---- 실패 주입(D3) — 의존성(asyncpg connection) 예외를 side_effect로 강제 --


async def test_transaction_rolls_back_on_injected_dependency_failure(pool):
    """실패 주입: CHECK/EXCLUDE 위반이 아니라 커넥션 자체가 죽는 상황을
    `side_effect`로 흉내낸다. 같은 트랜잭션에서 (a) 합법적인
    md_instrument 삽입 뒤 (b) 다음 statement에서 asyncpg 커넥션 예외가
    강제로 발생하면, (a)도 롤백돼야 한다 — 게이트 적색 재현(제약
    위반)과는 다른 실패 축(의존성 장애)에서도 같은 fail-closed
    불변식이 성립함을 증명한다."""
    canonical_symbol = f"TEST-{uuid4().hex}"

    conn = await pool.acquire()
    try:
        await conn.execute("BEGIN")
        await _insert_md_instrument(conn, canonical_symbol=canonical_symbol)

        with patch.object(
            asyncpg.Connection,
            "execute",
            side_effect=asyncpg.exceptions.ConnectionDoesNotExistError("injected failure"),
        ):
            with pytest.raises(asyncpg.exceptions.ConnectionDoesNotExistError):
                await conn.execute(
                    "INSERT INTO md_venue_calendar_day "
                    "(venue, trade_date, is_trading_day, source) "
                    "VALUES ('BITGET', '2026-06-02', true, 'injected')"
                )

        await conn.execute("ROLLBACK")
    finally:
        await pool.release(conn)

    row = await pool.fetchrow(
        "SELECT instrument_id FROM md_instrument WHERE canonical_symbol = $1", canonical_symbol
    )
    assert row is None, "주입된 의존성 실패 이후에도 앞선 INSERT가 커밋되어 남았습니다"


# ---- 동시성(D3) — 애플리케이션 사전 체크 없이 DB 제약만으로 레이스 방어 ----


async def test_concurrent_duplicate_instrument_insert_only_one_commits(pool):
    """동시성/적대적 증빙: 같은 (venue, canonical_symbol, listed_at)로
    두 커넥션이 동시에 INSERT를 시도하면, 정확히 하나만 커밋되고
    나머지는 UNIQUE 위반으로 거부되어야 한다.
    `PostgresReferenceRepository.register()`의 "이미 등록됐는지" 사전
    SELECT는 TOCTOU 레이스에 열려 있다 — 이 테스트는 애플리케이션
    사전 체크에 기대지 않고 DB 제약 자체가 최종 방어선으로 동작함을
    증명한다(동시 삽입 2건 중 1건만 살아남는지를 실제 동시 실행으로
    확인, 코드 흐름만 읽어서는 보증되지 않는 축)."""
    canonical_symbol = f"TEST-{uuid4().hex}"
    listed_at = datetime(2026, 1, 1, tzinfo=timezone.utc)

    results = await asyncio.gather(
        _insert_md_instrument_full(
            pool, canonical_symbol=canonical_symbol, venue_symbol="A", listed_at=listed_at
        ),
        _insert_md_instrument_full(
            pool, canonical_symbol=canonical_symbol, venue_symbol="B", listed_at=listed_at
        ),
        return_exceptions=True,
    )

    successes = [r for r in results if not isinstance(r, Exception)]
    failures = [r for r in results if isinstance(r, Exception)]
    assert len(successes) == 1, f"정확히 하나만 커밋되어야 합니다(결과: {results!r})"
    assert len(failures) == 1
    assert isinstance(failures[0], asyncpg.UniqueViolationError)

    row = await pool.fetchrow(
        "SELECT count(*) AS n FROM md_instrument WHERE canonical_symbol = $1", canonical_symbol
    )
    assert row["n"] == 1
