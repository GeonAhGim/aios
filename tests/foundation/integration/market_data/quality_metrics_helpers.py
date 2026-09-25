"""LA-18 quality_metrics/scheduler 통합테스트 — 실 DB(TEST_DATABASE_URL).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9.2 LA-18.
DoD(task-712): 스케줄러 1주기 후 게이지 존재, 심볼 1개 실패가 나머지 차단
안 함.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from src.data.models.base import AssetClass
from src.foundation.market_data.adapters.postgres_candle_store import PostgresCandleStore
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


def _clock(t0: datetime):
    def clock() -> datetime:
        return t0

    return clock


class _FakeIngestSource:
    def __init__(self, candles: list[CandleRecord]) -> None:
        self._candles = candles

    async def fetch_candles(self, venue, raw_symbol, tf, start, end):
        return list(self._candles)


class _FlakyCandleStore:
    """실제 `CandleStore`를 감싸되, 지정된 `instrument_id`에 대해서만
    `last_open_time`이 예외를 던진다 — 시계열 하나의 계산 실패를
    주입하기 위한 테스트 전용 래퍼(§9 LA-18 DoD)."""

    def __init__(self, inner: PostgresCandleStore, boom_instrument_id: uuid.UUID) -> None:
        self._inner = inner
        self._boom = boom_instrument_id

    async def last_open_time(self, conn, key):
        if key.instrument_id == self._boom:
            raise RuntimeError("injected store failure")
        return await self._inner.last_open_time(conn, key)

    async def upsert_batch(self, conn, batch_id, candles):
        return await self._inner.upsert_batch(conn, batch_id, candles)

    async def quarantine(self, conn, batch_id, candles, issues):
        return await self._inner.quarantine(conn, batch_id, candles, issues)

    async def query(self, conn, key, start, end, as_of):
        return await self._inner.query(conn, key, start, end, as_of)


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


async def _ingest(deps, instrument, candles, *, start, end, at):
    from src.foundation.market_data.application.ingest_candles import ingest_candles

    cmd = IngestCandlesCommand(
        tenant_id=None,
        venue=Venue.BITGET,
        canonical_symbol=instrument.canonical_symbol,
        timeframe=Timeframe.M1,
        range_start=start,
        range_end=end,
        trace_id=uuid.uuid4(),
    )
    return await ingest_candles(
        cmd,
        source=_FakeIngestSource(candles),
        store=deps.store,
        refs=deps.refs,
        cal=deps.cal,
        batches=deps.batches,
        audit=deps.audit,
        pool=deps.pool,
        clock=_clock(at),
    )


