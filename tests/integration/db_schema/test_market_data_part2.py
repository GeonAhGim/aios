"""3.x — DB 스키마 통합 테스트: LA-10(md reference registry)/LA-11(md candles) 절.

RATCHET-split(task-4222) — 원 `test_db_schema.py`(1176줄)에서 분리.
`db_conn`/`raw_conn` fixture는 `conftest.py`에서 자동 제공된다.
Spec: 04_db_schema_v1.7.md.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import asyncpg
import pytest

from tests.integration.db_schema.conftest import insert_audit_event

# --- LA-10 (4a1d0c0de007_md_reference_registry) ----------------------------

MD_REFERENCE_REGISTRY_TABLES = {
    "md_instrument",
    "md_symbol_alias",
    "md_corporate_action",
    "md_venue_calendar_day",
}


async def _insert_md_instrument(conn: asyncpg.Connection, *, canonical_symbol: str) -> object:
    return await conn.fetchval(
        "INSERT INTO md_instrument "
        "(venue, canonical_symbol, venue_symbol, asset_class, tick_size, lot_size, "
        " status, listed_at) "
        "VALUES ('BITGET', $1, $1, 'CRYPTO', 0.01, 0.0001, 'LISTED', now()) "
        "RETURNING instrument_id",
        canonical_symbol,
    )


# --- LA-11 (4a1d0c0de008_md_candles) ---------------------------------------

MD_CANDLES_TABLES = {
    "md_candle",
    "md_quarantine_candle",
    "md_tick",
    "md_ingest_batch",
    "md_quality_issue",
}


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
    open_time = open_time or datetime.now(UTC)
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
    open_time = datetime.now(UTC)
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
