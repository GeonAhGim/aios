"""test_ingest_candles.py 공용 헬퍼 — task-10465 분할.

loc_over_500 래칫: F1(M) tenant_id 전파 테스트 2건을 추가하면서 test_ingest_candles.py
가 540줄로 임계값을 넘겨, candle_store_support.py(CTO 2026-09-23)와 동일한 패턴으로
공용 픽스처/헬퍼를 분리했다. tenant_id 전파 테스트는 test_ingest_candles_tenant.py로
분리되었다.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from src.data.models.base import AssetClass
from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
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
)

__all__ = [
    "deps",
    "_BoomAuditAppender",
    "_FakeIngestSource",
    "_bitget_symbol",
    "_candle",
    "_clock",
    "_cmd",
    "_listed_instrument",
    "_run",
]


def _bitget_symbol() -> str:
    return f"T{uuid.uuid4().hex[:10].upper()}USDT"


def _candle(t: datetime, o: str, h: str, low: str, c: str, v: str) -> CandleRecord:
    return CandleRecord(
        key=SeriesKey(venue=Venue.BITGET, instrument_id=uuid.uuid4(), timeframe=Timeframe.M1),
        open_time=t,
        close_time=t + timedelta(minutes=1),
        open=Decimal(o),
        high=Decimal(h),
        low=Decimal(low),
        close=Decimal(c),
        volume=Decimal(v),
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
        venue=Venue.BITGET,
        venue_symbol=_bitget_symbol(),
        asset_class=AssetClass.CRYPTO,
        tick_size=Decimal("0.01"),
        lot_size=Decimal("0.0001"),
        listed_at=listed_at,
        actor_subject_id=uuid.uuid4(),
        trace_id=uuid.uuid4(),
    )
    instrument = await register_instrument(deps.pool, cmd, refs=deps.refs, audit=deps.audit)
    return await apply_lifecycle_event(
        deps.pool,
        LifecycleEventCommand(
            instrument_id=instrument.instrument_id,
            event="LIST",
            effective_at=datetime.now(timezone.utc),
            source_ref="test:list",
            actor_subject_id=uuid.uuid4(),
            trace_id=uuid.uuid4(),
        ),
        current=instrument,
        refs=deps.refs,
        audit=deps.audit,
    )


def _cmd(instrument, start: datetime, end: datetime) -> IngestCandlesCommand:
    return IngestCandlesCommand(
        tenant_id=None,
        venue=Venue.BITGET,
        canonical_symbol=instrument.canonical_symbol,
        timeframe=Timeframe.M1,
        range_start=start,
        range_end=end,
        trace_id=uuid.uuid4(),
    )


async def _run(deps, cmd, source, *, audit=None, clock_at):
    return await ingest_candles(
        cmd,
        source=source,
        store=deps.store,
        refs=deps.refs,
        cal=deps.cal,
        batches=deps.batches,
        audit=audit or deps.audit,
        pool=deps.pool,
        clock=_clock(clock_at),
    )
