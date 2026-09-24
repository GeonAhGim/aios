"""E2E-3(`test_e2e_3_market_data_to_chart.py`) 공용 헬퍼.

LOC 규율(CLAUDE.md §7, `scripts/code-ratchets-baseline.json` loc_over_500)
때문에 테스트 본문과 분리한다 — MockIngestSource(LA-9 계약 모의 어댑터)와
ingest_candles 호출을 위한 상장 상품/커맨드 조립은 E2E-3의 5개 시나리오
테스트가 공유하는 순수 준비 로직일 뿐 판정 로직이 아니므로, 여기로 옮겨도
시나리오 자체의 응집도를 해치지 않는다.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

from src.data.models.base import AssetClass
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
    Timeframe,
    Venue,
)
from src.foundation.market_data.ports.ingest_source import IngestSource


class MockIngestSource(IngestSource):
    """모의 ingest source: 계약 테스트용. LA-9 포트 구현."""

    def __init__(
        self,
        candles: list[CandleRecord] | None = None,
        fail_mode: str = "ok",
    ):
        self.candles = candles or []
        self.fail_mode = fail_mode
        self.fetch_calls: list[tuple[Venue, str, Timeframe, datetime, datetime]] = []

    async def fetch_candles(
        self,
        venue: Venue,
        raw_symbol: str,
        tf: Timeframe,
        start: datetime,
        end: datetime,
    ) -> list[CandleRecord]:
        """LA-9 IngestSource 포트 계약."""
        self.fetch_calls.append((venue, raw_symbol, tf, start, end))

        if self.fail_mode == "timeout":
            raise TimeoutError("ingest source did not respond within budget")
        if self.fail_mode == "http_error":
            raise RuntimeError("HTTP 500: upstream service unavailable")

        return self.candles


def _clock(t0: datetime):
    """테스트용 clock: 고정 시각."""

    def clock() -> datetime:
        return t0

    return clock


async def listed_instrument(deps: SimpleNamespace, venue: Venue, base_date: datetime) -> object:
    """테스트용 상장 상품 생성."""
    listed_at = base_date - timedelta(days=1)
    # BITGET symbols must end with USDT (crypto normalizer requirement)
    if venue is Venue.BITGET:
        symbol = f"T{uuid4().hex[:10].upper()}USDT"
    else:
        symbol = f"TEST{uuid4().hex[:8].upper()}"
    cmd = RegisterInstrumentCommand(
        venue=venue,
        venue_symbol=symbol,
        asset_class=AssetClass.CRYPTO,
        tick_size=Decimal("0.01"),
        lot_size=Decimal("0.0001"),
        listed_at=listed_at,
        actor_subject_id=uuid4(),
        trace_id=uuid4(),
    )
    instrument = await register_instrument(deps.pool, cmd, refs=deps.refs, audit=deps.audit)
    return await apply_lifecycle_event(
        deps.pool,
        LifecycleEventCommand(
            instrument_id=instrument.instrument_id,
            event="LIST",
            effective_at=base_date - timedelta(hours=1),
            source_ref="e2e-3:setup",
            actor_subject_id=uuid4(),
            trace_id=uuid4(),
        ),
        current=instrument,
        refs=deps.refs,
        audit=deps.audit,
    )


def ingest_cmd(
    instrument, start: datetime, end: datetime, *, venue: Venue = Venue.BITGET
) -> IngestCandlesCommand:
    """테스트용 ingest command."""
    return IngestCandlesCommand(
        tenant_id=None,
        venue=venue,
        canonical_symbol=instrument.canonical_symbol,
        timeframe=Timeframe.M1,
        range_start=start,
        range_end=end,
        trace_id=uuid4(),
    )


async def run_ingest(
    deps: SimpleNamespace,
    cmd: IngestCandlesCommand,
    source: IngestSource,
    *,
    clock_at: datetime,
) -> object:
    """테스트용 ingest_candles 호출."""
    return await ingest_candles(
        cmd,
        source=source,
        store=deps.store,
        refs=deps.refs,
        cal=deps.cal,
        batches=deps.batches,
        audit=deps.audit,
        pool=deps.pool,
        clock=_clock(clock_at),
    )


def simple_sma(closes: list[Decimal], period: int) -> list[Decimal | None]:
    """단순 이동평균(SMA) — 순수 함수, Decimal 정밀도 유지."""
    if period <= 0 or len(closes) < period:
        return [None] * len(closes)
    result: list[Decimal | None] = [None] * (period - 1)
    for i in range(period - 1, len(closes)):
        window = closes[i - period + 1 : i + 1]
        avg = sum(window) / Decimal(period)
        result.append(avg)
    return result
