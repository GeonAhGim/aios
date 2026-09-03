"""LA-15 ingest_candles/bitget_ingest_source 통합테스트 — 실 DB(TEST_DATABASE_URL) +
httpx.MockTransport(실키 금지).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§8.2, §9.2 LA-15.
DoD(task-623): 정상 배치 저장+배치행+감사 이벤트 1건; 재실행 저장 0·새 배치행;
DUPLICATE_CONFLICT 격리(기존 불변); OHLC 위반 캔들만 격리(나머지 정상 저장);
감사 실패 주입 → md_ingest_batch/md_candle/md_quarantine_candle/md_quality_issue
전부 롤백; bitget_ingest_source는 MockTransport로만 검증.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest

from src.data.models.base import AssetClass
from src.exchanges.bitget.adapter import BitgetAdapter
from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.market_data.adapters.bitget_ingest_source import BitgetIngestSource
from src.foundation.market_data.adapters.postgres_batch_repository import PostgresBatchRepository
from src.foundation.market_data.adapters.postgres_calendar_repository import (
    PostgresCalendarRepository,
)
from src.foundation.market_data.adapters.postgres_candle_store import PostgresCandleStore
from src.foundation.market_data.adapters.postgres_reference_repository import (
    PostgresReferenceRepository,
)
from src.foundation.market_data.application.ingest_candles import ingest_candles
from src.foundation.market_data.application.register_instrument import (
    apply_lifecycle_event,
    register_instrument,
)
from src.foundation.market_data.contracts.v1 import (
    CandleRecord,
    IngestCandlesCommand,
    LifecycleEventCommand,
    RegisterInstrumentCommand,
    SeriesKey,
    Timeframe,
    Venue,
    Verdict,
)


def _bitget_symbol() -> str:
    return f"T{uuid.uuid4().hex[:10].upper()}USDT"


def _candle(t: datetime, o: str, h: str, low: str, c: str, v: str) -> CandleRecord:
    return CandleRecord(
        key=SeriesKey(venue=Venue.BITGET, instrument_id=uuid.uuid4(), timeframe=Timeframe.M1),
        open_time=t, close_time=t + timedelta(minutes=1),
        open=Decimal(o), high=Decimal(h), low=Decimal(low), close=Decimal(c), volume=Decimal(v),
    )


class _FakeIngestSource:
    def __init__(self, candles: list[CandleRecord]) -> None:
        self._candles = candles

    async def fetch_candles(self, venue, raw_symbol, tf, start, end):
        return list(self._candles)


class _BoomAuditAppender:
    async def append_event_in(self, conn, **kwargs):
        raise RuntimeError("injected audit failure")


def _clock(t0: datetime):
    def clock() -> datetime:
        return t0

    return clock


@pytest.fixture
def deps(pool):
    return SimpleNamespace(
        pool=pool,
        refs=PostgresReferenceRepository(pool),
        cal=PostgresCalendarRepository(pool),
        audit=PostgresAuditEventRepository(pool),
        store=PostgresCandleStore(pool),
        batches=PostgresBatchRepository(pool),
    )


async def _listed_instrument(deps):
    listed_at = datetime.now(timezone.utc) - timedelta(days=1)
    cmd = RegisterInstrumentCommand(
        venue=Venue.BITGET, venue_symbol=_bitget_symbol(), asset_class=AssetClass.CRYPTO,
        tick_size=Decimal("0.01"), lot_size=Decimal("0.0001"), listed_at=listed_at,
        actor_subject_id=uuid.uuid4(), trace_id=uuid.uuid4(),
    )
    instrument = await register_instrument(deps.pool, cmd, refs=deps.refs, audit=deps.audit)
    return await apply_lifecycle_event(
        deps.pool,
        LifecycleEventCommand(
            instrument_id=instrument.instrument_id, event="LIST",
            effective_at=datetime.now(timezone.utc), source_ref="test:list",
            actor_subject_id=uuid.uuid4(), trace_id=uuid.uuid4(),
        ),
        current=instrument, refs=deps.refs, audit=deps.audit,
    )


def _cmd(instrument, start: datetime, end: datetime) -> IngestCandlesCommand:
    return IngestCandlesCommand(
        tenant_id=None, venue=Venue.BITGET, canonical_symbol=instrument.canonical_symbol,
        timeframe=Timeframe.M1, range_start=start, range_end=end, trace_id=uuid.uuid4(),
    )


async def _run(deps, cmd, source, *, audit=None, clock_at):
    return await ingest_candles(
        cmd, source=source, store=deps.store, refs=deps.refs, cal=deps.cal,
        batches=deps.batches, audit=audit or deps.audit, pool=deps.pool, clock=_clock(clock_at),
    )


async def test_ingest_accepts_and_stores_batch_with_one_audit_event(pool, deps):
    instrument = await _listed_instrument(deps)
    t0 = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    candles = [
        _candle(t0, "100", "110", "90", "105", "10"),
        _candle(t0 + timedelta(minutes=1), "105", "115", "95", "110", "12"),
    ]
    result = await _run(
        deps, _cmd(instrument, t0, t0 + timedelta(minutes=2)), _FakeIngestSource(candles),
        clock_at=t0,
    )

    assert result.verdict.verdict == Verdict.ACCEPT
    assert result.verdict.accepted == 2
    async with pool.acquire() as conn:
        stored = await conn.fetchval(
            "SELECT COUNT(*) FROM md_candle WHERE batch_id = $1", result.batch_id
        )
        events = await conn.fetchval(
            "SELECT COUNT(*) FROM foundation_audit_event WHERE aggregate_id = $1", result.batch_id
        )
        batch_rows = await conn.fetchval(
            "SELECT COUNT(*) FROM md_ingest_batch WHERE id = $1", result.batch_id
        )
    assert stored == 2
    assert events == 1
    assert batch_rows == 1


async def test_ingest_reingest_is_idempotent_and_creates_new_batch_row(pool, deps):
    instrument = await _listed_instrument(deps)
    t0 = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    candles = [_candle(t0, "100", "110", "90", "105", "10")]
    cmd = _cmd(instrument, t0, t0 + timedelta(minutes=1))

    first = await _run(deps, cmd, _FakeIngestSource(candles), clock_at=t0)
    second = await _run(deps, cmd, _FakeIngestSource(candles), clock_at=t0)

    assert first.batch_id != second.batch_id
    async with pool.acquire() as conn:
        candle_count = await conn.fetchval(
            "SELECT COUNT(*) FROM md_candle WHERE instrument_id = $1", instrument.instrument_id
        )
        batch_count = await conn.fetchval(
            "SELECT COUNT(*) FROM md_ingest_batch WHERE instrument_id = $1",
            instrument.instrument_id,
        )
    assert candle_count == 1, "재수집은 멱등해야 한다(같은 캔들이 두 번 저장되지 않음)"
    assert batch_count == 2, "배치 기록 자체는 호출마다 새로 남는다(md_ingest_batch는 INSERT only)"


async def test_ingest_quarantines_duplicate_conflict_candles(pool, deps):
    instrument = await _listed_instrument(deps)
    t0 = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    conflicting = [
        _candle(t0, "100", "110", "90", "105", "10"),
        _candle(t0, "100", "120", "90", "115", "10"),  # 같은 open_time, 내용 다름
    ]
    result = await _run(
        deps, _cmd(instrument, t0, t0 + timedelta(minutes=1)), _FakeIngestSource(conflicting),
        clock_at=t0,
    )

    async with pool.acquire() as conn:
        stored = await conn.fetchval(
            "SELECT COUNT(*) FROM md_candle WHERE instrument_id = $1 AND open_time = $2",
            instrument.instrument_id, t0,
        )
        quarantined = await conn.fetchval(
            "SELECT COUNT(*) FROM md_quarantine_candle "
            "WHERE batch_id = $1 AND issue_type = 'DUPLICATE_CONFLICT'",
            result.batch_id,
        )
    assert stored == 0, "충돌 캔들은 정상 테이블에 남지 않는다(기존 불변)"
    assert quarantined >= 1


async def test_ingest_quarantines_ohlc_violation_candle_and_stores_rest(pool, deps):
    instrument = await _listed_instrument(deps)
    t0 = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    good = [_candle(t0 + timedelta(minutes=i), "100", "110", "90", "105", "10") for i in range(4)]
    bad = _candle(t0 + timedelta(minutes=4), "100", "90", "80", "85", "10")  # high(90) < open(100)
    result = await _run(
        deps, _cmd(instrument, t0, t0 + timedelta(minutes=5)), _FakeIngestSource([*good, bad]),
        clock_at=t0,
    )

    assert result.verdict.verdict == Verdict.PARTIAL
    async with pool.acquire() as conn:
        stored = await conn.fetchval(
            "SELECT COUNT(*) FROM md_candle WHERE batch_id = $1", result.batch_id
        )
        quarantined = await conn.fetchval(
            "SELECT COUNT(*) FROM md_quarantine_candle "
            "WHERE batch_id = $1 AND issue_type = 'OHLC_INCONSISTENT'",
            result.batch_id,
        )
    assert stored == 4
    assert quarantined == 1


async def test_ingest_rolls_back_everything_on_audit_failure(pool, deps):
    instrument = await _listed_instrument(deps)
    t0 = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    candles = [_candle(t0, "100", "110", "90", "105", "10")]
    cmd = _cmd(instrument, t0, t0 + timedelta(minutes=1))

    with pytest.raises(RuntimeError):
        await _run(
            deps, cmd, _FakeIngestSource(candles), audit=_BoomAuditAppender(), clock_at=t0
        )

    async with pool.acquire() as conn:
        candle_count = await conn.fetchval(
            "SELECT COUNT(*) FROM md_candle WHERE instrument_id = $1", instrument.instrument_id
        )
        batch_count = await conn.fetchval(
            "SELECT COUNT(*) FROM md_ingest_batch WHERE instrument_id = $1",
            instrument.instrument_id,
        )
        issue_count = await conn.fetchval(
            "SELECT COUNT(*) FROM md_quality_issue WHERE batch_id IN "
            "(SELECT id FROM md_ingest_batch WHERE instrument_id = $1)",
            instrument.instrument_id,
        )
    assert candle_count == 0
    assert batch_count == 0
    assert issue_count == 0


async def test_bitget_ingest_source_fetches_and_filters_via_mock_transport():
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    ts_ms = int(t0.timestamp() * 1000)
    rows = [
        [str(ts_ms), "100", "110", "90", "105", "10", "1000", "1000"],
        [str(ts_ms + 60_000), "105", "115", "95", "110", "12", "1200", "1200"],
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/spot/market/candles"
        assert request.url.params.get("symbol") == "BTCUSDT"
        return httpx.Response(200, json={"code": "00000", "msg": "success", "data": rows})

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(base_url="https://api.bitget.com", transport=transport)
    adapter = BitgetAdapter("key", "secret", "passphrase", demo_mode=True, http_client=client)
    source = BitgetIngestSource(adapter)

    result = await source.fetch_candles(
        Venue.BITGET, "BTCUSDT", Timeframe.M1, t0, t0 + timedelta(minutes=1)
    )

    assert len(result) == 1
    assert result[0].open_time == t0
    assert result[0].close == Decimal("105")
