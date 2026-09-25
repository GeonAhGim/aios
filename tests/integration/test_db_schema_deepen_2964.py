"""LA-10 md_reference_registry — DEEPEN(task-2964, DEPTH_LA_LB_LC 소급감사
task-2723/task-418) D1 -> D2 증빙.

`test_db_schema.py`의 LA-10 절은 4개 CHECK/EXCLUDE negative만 증명했다
(D1) — 감사 판정(docs/audit/DEPTH_LA_LB_LC.md #418)에서 지적된 부족분은
정확히 두 가지: 수치 성능 단언 없음, 게이트 적색 재현 없음. 이 파일은
그 두 가지만 닫는다. 새 기능 없음, 깊이만 올린다.

`md_symbol_alias`의 `EXCLUDE USING gist`는 삽입마다 겹침 검사를 위해
GiST 인덱스를 스캔한다 — 인덱스가 없거나 퇴화하면 O(n) 순차비교로
느려진다(DC-4 venue_listings와 동일한 리스크, 04_db_schema_v1.7.md
동일 패턴). 성능 단언은 이 인덱스가 실제로 타는지를 절대시간 예산으로
증명한다.

게이트 적색 재현은 같은 트랜잭션 안에서 (a) 합법적인 md_instrument
삽입 뒤 (b) md_corporate_action의 CHECK(ratio > 0) 위반을 일으키면,
트랜잭션 전체가 롤백돼 (a)도 커밋되지 않아야 함을 증명한다 — 부분
커밋으로 인한 참조무결성 오염 방지.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone
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


# ---- 수치 성능 단언(D2) ----------------------------------------------------


@pytest.mark.perf
async def test_bulk_non_overlapping_alias_inserts_meet_latency_budget(pool):
    """500개 서로 겹치지 않는 (venue, alias_symbol) 별칭을 같은 심볼에
    연속 삽입해 절대시간 예산 내임을 단언한다(실측 로컬 <2s, 예산은
    느린 CI 대비 넉넉히 잡음). EXCLUDE USING gist가 GiST 인덱스를 타지
    못하고 순차 스캔으로 퇴화하면 이 예산을 넘긴다."""
    instrument_id = await _insert_md_instrument(pool, canonical_symbol=f"TEST-{uuid4().hex}")
    alias_symbol = f"ALIAS-{uuid4().hex}"
    t0 = datetime(2000, 1, 1, tzinfo=timezone.utc)
    n = 500
    budget_sec = 15.0

    start = time.perf_counter()
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
    elapsed = time.perf_counter() - start
    print(
        f"[LA-10 md_symbol_alias] {n} inserts in {elapsed:.3f}s "
        f"({elapsed / n * 1e3:.2f} ms/insert, budget<{budget_sec}s)"
    )
    assert elapsed < budget_sec, (
        f"md_symbol_alias {n}건 삽입이 예산({budget_sec}s)을 넘었습니다"
        f"({elapsed:.3f}s) — GiST 인덱스가 안 타는지 확인하세요."
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
