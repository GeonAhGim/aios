"""3.x — DB 스키마 통합 테스트: LA-10(md reference registry)/LA-11(md candles) 절.

RATCHET-split(task-4222) — 원 `test_db_schema.py`(1176줄)에서 분리.
`db_conn`/`raw_conn` fixture는 `conftest.py`에서 자동 제공된다.
Spec: 04_db_schema_v1.7.md.
"""

import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import asyncpg
import pytest
from sqlalchemy import text

from tests.integration.db_schema.conftest import insert_audit_event

# --- LA-10 (4a1d0c0de007_md_reference_registry) ----------------------------

MD_REFERENCE_REGISTRY_TABLES = {
    "md_instrument",
    "md_symbol_alias",
    "md_corporate_action",
    "md_venue_calendar_day",
}


async def test_md_reference_registry_tables_exist(db_conn):
    result = await db_conn.execute(
        text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = ANY(:names)"
        ),
        {"names": list(MD_REFERENCE_REGISTRY_TABLES)},
    )
    found = {row[0] for row in result}
    assert found == MD_REFERENCE_REGISTRY_TABLES


async def _insert_md_instrument(conn: asyncpg.Connection, *, canonical_symbol: str) -> object:
    return await conn.fetchval(
        "INSERT INTO md_instrument "
        "(venue, canonical_symbol, venue_symbol, asset_class, tick_size, lot_size, "
        " status, listed_at) "
        "VALUES ('BITGET', $1, $1, 'CRYPTO', 0.01, 0.0001, 'LISTED', now()) "
        "RETURNING instrument_id",
        canonical_symbol,
    )


async def test_md_instrument_invalid_status_rejected(raw_conn):
    """LA-10 DoD — status CHECK negative: `SymbolStatus`(§3.1)에 없는 값은
    DB 레벨에서 거부되어야 한다."""
    with pytest.raises(asyncpg.CheckViolationError):
        await raw_conn.execute(
            "INSERT INTO md_instrument "
            "(venue, canonical_symbol, venue_symbol, asset_class, tick_size, lot_size, "
            " status, listed_at) "
            "VALUES ('BITGET', $1, $1, 'CRYPTO', 0.01, 0.0001, 'BOGUS_STATUS', now())",
            f"TEST-{uuid4().hex}",
        )


async def test_md_symbol_alias_overlapping_period_rejected(raw_conn):
    """LA-10 DoD — `EXCLUDE USING gist` negative(btree_gist): 같은
    (venue, alias_symbol)의 유효기간이 겹치면 두 번째 별칭 insert가
    거부되어야 한다."""
    instrument_id = await _insert_md_instrument(raw_conn, canonical_symbol=f"TEST-{uuid4().hex}")
    alias_symbol = f"ALIAS-{uuid4().hex}"
    valid_from = datetime(2026, 1, 1, tzinfo=timezone.utc)

    await raw_conn.execute(
        "INSERT INTO md_symbol_alias (instrument_id, venue, alias_symbol, valid_from) "
        "VALUES ($1, 'BITGET', $2, $3)",
        instrument_id,
        alias_symbol,
        valid_from,
    )
    with pytest.raises(asyncpg.ExclusionViolationError):
        await raw_conn.execute(
            "INSERT INTO md_symbol_alias (instrument_id, venue, alias_symbol, valid_from) "
            "VALUES ($1, 'BITGET', $2, $3)",
            instrument_id,
            alias_symbol,
            valid_from + timedelta(days=1),
        )


async def test_md_corporate_action_non_positive_ratio_rejected(raw_conn):
    """LA-10 DoD — CHECK(ratio > 0) negative."""
    instrument_id = await _insert_md_instrument(raw_conn, canonical_symbol=f"TEST-{uuid4().hex}")
    with pytest.raises(asyncpg.CheckViolationError):
        await raw_conn.execute(
            "INSERT INTO md_corporate_action "
            "(instrument_id, action_type, ex_date, ratio, source_ref) "
            "VALUES ($1, 'SPLIT', '2026-06-01', 0, 'test')",
            instrument_id,
        )


async def test_md_venue_calendar_day_trading_flag_mismatch_rejected(raw_conn):
    """LA-10 DoD — CHECK(is_trading_day = (open_at IS NOT NULL)) negative."""
    with pytest.raises(asyncpg.CheckViolationError):
        await raw_conn.execute(
            "INSERT INTO md_venue_calendar_day "
            "(venue, trade_date, is_trading_day, open_at, source) "
            "VALUES ('BITGET', '2026-06-01', true, NULL, 'test')"
        )


# --- LA-11 (4a1d0c0de008_md_candles) ---------------------------------------

MD_CANDLES_TABLES = {
    "md_candle",
    "md_quarantine_candle",
    "md_tick",
    "md_ingest_batch",
    "md_quality_issue",
}


async def test_md_candles_tables_exist(db_conn):
    result = await db_conn.execute(
        text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = ANY(:names)"
        ),
        {"names": list(MD_CANDLES_TABLES)},
    )
    found = {row[0] for row in result}
    assert found == MD_CANDLES_TABLES


async def _insert_md_ingest_batch(
    conn: asyncpg.Connection, *, instrument_id, verdict: str = "ACCEPT"
) -> object:
    audit_event_id = await insert_audit_event(conn)
    return await conn.fetchval(
        "INSERT INTO md_ingest_batch "
        "(source, venue, instrument_id, timeframe, range_start, range_end, "
        " request_fingerprint, batch_hash, verdict, audit_event_id) "
        "VALUES ('test', 'BITGET', $1, '1m', now(), now(), $2, $3, $4, $5) "
        "RETURNING id",
        instrument_id,
        f"fp-{uuid4().hex}",
        f"hash-{uuid4().hex}",
        verdict,
        audit_event_id,
    )


async def _md_candles_setup(conn: asyncpg.Connection) -> tuple[object, object]:
    instrument_id = await _insert_md_instrument(conn, canonical_symbol=f"TEST-{uuid4().hex}")
    batch_id = await _insert_md_ingest_batch(conn, instrument_id=instrument_id)
    return instrument_id, batch_id


async def _insert_md_candle(
    conn: asyncpg.Connection,
    *,
    instrument_id,
    batch_id,
    open_: float,
    high: float,
    low: float,
    close: float,
    volume: float,
    open_time: datetime | None = None,
) -> None:
    open_time = open_time or datetime.now(timezone.utc)
    close_time = open_time + timedelta(minutes=1)
    await conn.execute(
        "INSERT INTO md_candle "
        "(venue, instrument_id, timeframe, open_time, close_time, "
        " open, high, low, close, volume, batch_id) "
        "VALUES ('BITGET', $1, '1m', $2, $3, $4, $5, $6, $7, $8, $9)",
        instrument_id,
        open_time,
        close_time,
        open_,
        high,
        low,
        close,
        volume,
        batch_id,
    )


@pytest.mark.parametrize(
    "check_name,ohlcv",
    [
        ("ck_md_candle_high_ge_open", (100, 90, 80, 85, 10)),
        ("ck_md_candle_high_ge_close", (80, 90, 70, 100, 10)),
        ("ck_md_candle_high_ge_low", (50, 60, 70, 50, 10)),
        ("ck_md_candle_low_le_open", (50, 80, 60, 70, 10)),
        ("ck_md_candle_low_le_close", (80, 90, 70, 60, 10)),
        ("ck_md_candle_volume_nonneg", (100, 110, 90, 105, -1)),
    ],
)
async def test_md_candle_ohlcv_check_violations_rejected(raw_conn, check_name, ohlcv):
    """LA-11 DoD — §4.1 CHECK 6종 negative(실값): 각 부등식을 하나씩만
    위반하는 (open, high, low, close, volume) 조합으로 INSERT가 거부되는지
    증명한다(스킵 금지). 값 조합은 실제 asyncpg 세션으로 사전 검증됨
    (task-450 note) — 각 케이스는 해당 CHECK가 다른 5종보다 먼저 평가되도록
    골랐다."""
    instrument_id, batch_id = await _md_candles_setup(raw_conn)
    open_, high, low, close, volume = ohlcv
    with pytest.raises(asyncpg.CheckViolationError, match=check_name):
        await _insert_md_candle(
            raw_conn,
            instrument_id=instrument_id,
            batch_id=batch_id,
            open_=open_,
            high=high,
            low=low,
            close=close,
            volume=volume,
        )


async def test_md_candle_valid_ohlcv_accepted(raw_conn):
    """위 6종 CHECK 테스트의 대조군 — 정상 캔들까지 잘못 막지 않는지 확인."""
    instrument_id, batch_id = await _md_candles_setup(raw_conn)
    await _insert_md_candle(
        raw_conn,
        instrument_id=instrument_id,
        batch_id=batch_id,
        open_=100,
        high=110,
        low=90,
        close=105,
        volume=10,
    )
    row = await raw_conn.fetchrow(
        "SELECT open FROM md_candle WHERE instrument_id = $1", instrument_id
    )
    assert row is not None


async def test_aios_app_cannot_update_md_candle(raw_conn):
    """LA-11 DoD — `md_candle`은 WORM: 파티션 부모에 건 append-only 가드가
    (지금 달 파티션이 아니라) 미래 달 파티션에 저장된 행에도 적용되는지까지
    함께 증명한다(PG11+ 트리거 클로닝, 마이그레이션 docstring 참조)."""
    instrument_id, batch_id = await _md_candles_setup(raw_conn)
    future_open_time = datetime.now(timezone.utc) + timedelta(days=90)
    # 마이그레이션 upgrade()가 만든 파티션은 +3개월까지뿐이라 90일 뒤가 그
    # 경계를 넘을 수 있다(월별 경계는 날짜와 무관) — 먼저 여유 있게 확장한다.
    await raw_conn.execute("SELECT md_ensure_partitions(6)")
    await _insert_md_candle(
        raw_conn,
        instrument_id=instrument_id,
        batch_id=batch_id,
        open_=100,
        high=110,
        low=90,
        close=105,
        volume=10,
        open_time=future_open_time,
    )

    with pytest.raises(asyncpg.RaiseError, match="append-only violation"):
        async with raw_conn.transaction():
            await raw_conn.execute("SET ROLE aios_app")
            await raw_conn.execute(
                "UPDATE md_candle SET volume = 999 WHERE instrument_id = $1", instrument_id
            )


async def test_aios_app_cannot_delete_md_ingest_batch(raw_conn):
    """LA-11 DoD — `md_ingest_batch`도 WORM 대상(명세 §9.2 LA-11 표)."""
    instrument_id = await _insert_md_instrument(raw_conn, canonical_symbol=f"TEST-{uuid4().hex}")
    batch_id = await _insert_md_ingest_batch(raw_conn, instrument_id=instrument_id)

    with pytest.raises(asyncpg.RaiseError, match="append-only violation"):
        async with raw_conn.transaction():
            await raw_conn.execute("SET ROLE aios_app")
            await raw_conn.execute("DELETE FROM md_ingest_batch WHERE id = $1", batch_id)


async def test_aios_app_cannot_update_md_quality_issue(raw_conn):
    """LA-11 DoD — `md_quality_issue`도 WORM 대상."""
    instrument_id = await _insert_md_instrument(raw_conn, canonical_symbol=f"TEST-{uuid4().hex}")
    batch_id = await _insert_md_ingest_batch(raw_conn, instrument_id=instrument_id)
    issue_id = await raw_conn.fetchval(
        "INSERT INTO md_quality_issue (batch_id, type, severity, detail) "
        "VALUES ($1, 'GAP', 'WARN', '{}'::jsonb) RETURNING id",
        batch_id,
    )

    with pytest.raises(asyncpg.RaiseError, match="append-only violation"):
        async with raw_conn.transaction():
            await raw_conn.execute("SET ROLE aios_app")
            await raw_conn.execute(
                "UPDATE md_quality_issue SET severity = 'REJECT' WHERE id = $1", issue_id
            )


async def test_md_ensure_partitions_creates_future_partitions(raw_conn):
    """LA-11 DoD — `md_ensure_partitions(months_ahead)` 호출이 실제로 새
    파티션을 만드는지 확인한다: 마이그레이션이 이미 만들어 둔 범위(3개월)를
    넘어서는 달의 파티션을 요청해 그 전에는 없었다가 호출 후 생겼는지
    증명한다. `aios_app`으로 호출해 SECURITY DEFINER가 실제로 필요한지도
    함께 검증한다(런타임 role은 스키마 CREATE 권한이 없다)."""
    before = await raw_conn.fetch(
        "SELECT c.relname FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid "
        "WHERE i.inhparent = 'md_candle'::regclass"
    )
    before_names = {row["relname"] for row in before}
    # 같은 TEST_DATABASE_URL을 다른 테스트(위 test_aios_app_cannot_update_md_candle
    # 등)와 공유해 이미 몇 달치가 만들어져 있을 수 있다 — 지금 있는 것보다
    # 확실히 더 먼 미래를 요청해야 "새로 생겼다"는 판정이 순서 독립적이다.
    months_ahead = len(before_names) + 2

    async with raw_conn.transaction():
        await raw_conn.execute("SET ROLE aios_app")
        await raw_conn.execute("SELECT md_ensure_partitions($1)", months_ahead)

    after = await raw_conn.fetch(
        "SELECT c.relname FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid "
        "WHERE i.inhparent = 'md_candle'::regclass"
    )
    after_names = {row["relname"] for row in after}
    assert after_names - before_names, "md_ensure_partitions()가 새 파티션을 만들지 않았다"


@pytest.mark.perf
async def test_md_candle_bulk_insert_p95_under_budget(raw_conn):
    """LA-11 DEEPEN(task-2966, docs/audit/DEPTH_LA_LB_LC.md#450) — 수치
    성능 단언. §4.1 배치 인제스트는 캔들마다 md_candle에 개별 INSERT를
    낸다(CHECK 6종 평가 포함, WORM 트리거는 BEFORE UPDATE OR DELETE만이라
    INSERT 경로엔 붙지 않는다) — 이 핫 경로의 p95 지연이 예산 안에 있는지
    증명한다. 예산은 해시체인 계산까지 포함하는 원장 append p95 30ms
    (task-489/LB-18, task-614/LC-17) 관행과 같은 자릿수를 쓴다: md_candle
    INSERT는 해시체인이 없어 그보다 가벼워야 하지만, 공유 로컬 Postgres
    편차를 감안해 같은 예산을 그대로 적용한다."""
    instrument_id, batch_id = await _md_candles_setup(raw_conn)
    n = 200
    budget_p95_sec = 0.03
    base_time = datetime.now(timezone.utc)
    latencies: list[float] = []
    for i in range(n):
        start = time.perf_counter()
        await _insert_md_candle(
            raw_conn,
            instrument_id=instrument_id,
            batch_id=batch_id,
            open_=100,
            high=110,
            low=90,
            close=105,
            volume=10,
            open_time=base_time + timedelta(minutes=i),
        )
        latencies.append(time.perf_counter() - start)

    latencies.sort()
    p95 = latencies[int(n * 0.95)]
    print(
        f"[LA-11 md_candle insert] n={n} p95={p95 * 1000:.2f}ms "
        f"(budget<{budget_p95_sec * 1000:.0f}ms)"
    )
    assert p95 < budget_p95_sec, f"md_candle 단건 INSERT p95가 예산을 넘었습니다: {p95:.4f}s"


async def test_md_candle_lifecycle_replayed_quarantine_then_accept_does_not_leak(raw_conn):
    """LA-11 DEEPEN(task-2966, docs/audit/DEPTH_LA_LB_LC.md#450) — 게이트
    적색 재현. 동일 (instrument, timeframe, open_time)를 시간축으로
    재생한다: 1차 배치가 OHLC 위반으로 REJECT되어 md_quarantine_candle에만
    격리 -> 2차 배치가 같은 키로 정상 재인입되어 md_candle에 안착한다.
    1차 격리분이 md_candle로 새거나(격리 우회), 2차 정상분이 격리 이력을
    지워버리면(감사 흔적 소실) §4.1 품질 게이트가 무의미해진다 — 두 단계
    사이에 상태가 새지 않고, 승격된 행에도 WORM이 그대로 걸리는지까지
    증명한다."""
    instrument_id = await _insert_md_instrument(raw_conn, canonical_symbol=f"TEST-{uuid4().hex}")
    open_time = datetime.now(timezone.utc)
    close_time = open_time + timedelta(minutes=1)

    # 1단계: 1차 배치 REJECT — 위반 캔들은 md_quarantine_candle에만 격리.
    batch1_id = await _insert_md_ingest_batch(
        raw_conn, instrument_id=instrument_id, verdict="REJECT"
    )
    await raw_conn.execute(
        "INSERT INTO md_quarantine_candle "
        "(venue, instrument_id, timeframe, open_time, close_time, "
        " open, high, low, close, volume, batch_id, issue_type) "
        "VALUES ('BITGET', $1, '1m', $2, $3, 100, 90, 80, 85, 10, $4, 'OHLC_INCONSISTENT')",
        instrument_id,
        open_time,
        close_time,
        batch1_id,
    )

    leaked = await raw_conn.fetchval(
        "SELECT count(*) FROM md_candle WHERE instrument_id = $1 AND open_time = $2",
        instrument_id,
        open_time,
    )
    assert leaked == 0, "REJECT 배치의 격리분이 md_candle로 샜다"

    # 2단계: 2차 배치 ACCEPT — 같은 키로 정상 캔들 재인입.
    batch2_id = await _insert_md_ingest_batch(raw_conn, instrument_id=instrument_id)
    await _insert_md_candle(
        raw_conn,
        instrument_id=instrument_id,
        batch_id=batch2_id,
        open_=100,
        high=110,
        low=90,
        close=105,
        volume=10,
        open_time=open_time,
    )

    accepted = await raw_conn.fetchrow(
        "SELECT batch_id FROM md_candle WHERE instrument_id = $1 AND open_time = $2",
        instrument_id,
        open_time,
    )
    assert accepted is not None
    assert accepted["batch_id"] == batch2_id

    quarantine_count = await raw_conn.fetchval(
        "SELECT count(*) FROM md_quarantine_candle WHERE instrument_id = $1 AND open_time = $2",
        instrument_id,
        open_time,
    )
    assert quarantine_count == 1, "1차 격리 이력이 2차 정상 인입으로 지워지면 안 된다"

    # 3단계: 재생 직후에도 WORM이 그대로 걸려 있는지 재확인 — 재인입 경로가
    # append-only 보호를 우회하는 부작용을 남기지 않았는지 증명한다.
    with pytest.raises(asyncpg.RaiseError, match="append-only violation"):
        async with raw_conn.transaction():
            await raw_conn.execute("SET ROLE aios_app")
            await raw_conn.execute(
                "UPDATE md_candle SET volume = 999 WHERE instrument_id = $1 AND open_time = $2",
                instrument_id,
                open_time,
            )
