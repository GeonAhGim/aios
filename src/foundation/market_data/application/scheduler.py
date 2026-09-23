"""LA-18 — market_data scheduler: periodic per-symbol×timeframe ingest (optional)
+ quality metrics export.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§5.3, §9.2 LA-18.

Follows the same design principles as `src/services/execution_loop/scheduler.py`
and `src/foundation/ledger/application/scheduler.py` — a failure (exception) in
one cycle does not kill the loop; it retries on the next cycle, and a failure
in one item (one watched series) does not block the rest (§9 LA-18 DoD).

Deviation: the spec table writes the signature as `async def
run_market_data_scheduler(app_state, *, interval_s, stop: asyncio.Event)` a
free function, but the two already-merged same-layer schedulers
(`ExecutionLoopScheduler`, `LedgerIntegrityScheduler`) all use the class +
`run_forever()` method pattern, so we follow that (the main.py wiring point
already assumes that pattern — task-712 decision).

The ingest portion has no port/config in this leaf scope to decide "which
symbols to collect periodically" (not among LA-9's 5 ports, not in config),
so it operates only on the `watched: Sequence[WatchedSeries]` passed explicitly
by the caller (default empty sequence — performs quality metrics export only).
Operational symbol lists and credential wiring are subsequent (§10, undetermined).
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import asyncpg

from src.core.observability.metrics_registry import MetricsRegistry
from src.foundation.market_data.application.ingest_candles import AuditAppender, ingest_candles
from src.foundation.market_data.application.quality_metrics import export_quality_metrics
from src.foundation.market_data.contracts.v1 import (
    DataQualityMetrics,
    IngestCandlesCommand,
    Timeframe,
    Venue,
)
from src.foundation.market_data.ports.batch_repository import BatchRepository
from src.foundation.market_data.ports.calendar_repository import CalendarRepository
from src.foundation.market_data.ports.candle_store import CandleStore
from src.foundation.market_data.ports.ingest_source import IngestSource
from src.foundation.market_data.ports.reference_repository import ReferenceRepository

__all__ = ["Clock", "MarketDataQualityScheduler", "WatchedSeries"]

logger = logging.getLogger(__name__)

Clock = Callable[[], datetime]

DEFAULT_INTERVAL_SECONDS = 60.0


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class WatchedSeries:
    """(venue, symbol, timeframe) the scheduler re-fetches each cycle. `lookback`
    re-collects `[now - lookback, now)` every cycle — `md_candle` uses `ON
    CONFLICT DO NOTHING` (§5), so repeating overlapping ranges is safe."""

    venue: Venue
    canonical_symbol: str
    timeframe: Timeframe
    tenant_id: UUID | None = None
    lookback: timedelta = timedelta(hours=1)


@dataclass
class CycleReport:
    ingested: list[str] = field(default_factory=list)
    ingest_failed: dict[str, str] = field(default_factory=dict)
    metrics: list[DataQualityMetrics] = field(default_factory=list)


class MarketDataQualityScheduler:
    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        store: CandleStore,
        refs: ReferenceRepository,
        cal: CalendarRepository,
        batches: BatchRepository,
        registry: MetricsRegistry,
        source: IngestSource | None = None,
        audit: AuditAppender | None = None,
        watched: Sequence[WatchedSeries] = (),
        interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
        clock: Clock = _utcnow,
    ) -> None:
        if watched and (source is None or audit is None):
            raise ValueError(
                "watched가 비어있지 않으면 ingest에 필요한 source·audit이 둘 다 필요하다"
            )
        self._pool = pool
        self._store = store
        self._refs = refs
        self._cal = cal
        self._batches = batches
        self._registry = registry
        self._source = source
        self._audit = audit
        self._watched = list(watched)
        self.interval_seconds = interval_seconds
        self._clock: Clock = clock

    async def _ingest_one(self, target: WatchedSeries, now: datetime) -> None:
        assert self._source is not None and self._audit is not None  # invariant from __init__
        cmd = IngestCandlesCommand(
            tenant_id=target.tenant_id,
            venue=target.venue,
            canonical_symbol=target.canonical_symbol,
            timeframe=target.timeframe,
            range_start=now - target.lookback,
            range_end=now,
            trace_id=uuid4(),
        )
        await ingest_candles(
            cmd,
            source=self._source,
            store=self._store,
            refs=self._refs,
            cal=self._cal,
            batches=self._batches,
            audit=self._audit,
            pool=self._pool,
            clock=lambda: now,
        )

    async def run_once(self) -> CycleReport:
        """One cycle: ingest watched series (failure-isolated per item) → quality gauge export.

        An ingest failure does not block this cycle's export — export only walks
        already-stored batches, so data that did not arrive this cycle is itself
        reflected in the STALE gauge (§4.1)."""
        now = self._clock()
        report = CycleReport()
        for target in self._watched:
            label = f"{target.venue.value}:{target.canonical_symbol}:{target.timeframe.value}"
            try:
                await self._ingest_one(target, now)
            except Exception as exc:
                report.ingest_failed[label] = f"{type(exc).__name__}: {exc}"
                logger.exception(
                    "market_data_scheduler: %s ingest 실패 — 다음 주기에 재시도", label
                )
                continue
            report.ingested.append(label)

        report.metrics = await export_quality_metrics(
            batches=self._batches,
            store=self._store,
            cal=self._cal,
            pool=self._pool,
            registry=self._registry,
            clock=self._clock,
        )
        return report

    async def run_forever(self) -> None:
        """main.py background task body. A full-cycle failure does not kill the
        loop (same design as execution_loop/scheduler.py)."""
        while True:
            await asyncio.sleep(self.interval_seconds)
            try:
                await self.run_once()
            except Exception:
                logger.exception("market_data_scheduler: 이번 주기 전체 실패 — 다음 주기에 재시도")
