"""LA-18 — market_data 스케줄러: 심볼×tf별 주기 ingest(선택) + 품질 지표 export.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§5.3, §9.2 LA-18.

`src/services/execution_loop/scheduler.py`·`src/foundation/ledger/application/
scheduler.py`와 같은 설계 원칙을 따른다 — 한 주기의 실패(예외)가 루프 자체를
죽이지 않고 다음 주기에 다시 시도하며, 항목 하나(watched series 하나)의
실패가 나머지를 막지 않는다(§9 LA-18 DoD).

편차: 명세 표는 시그니처를 `async def run_market_data_scheduler(app_state, *,
interval_s, stop: asyncio.Event)` 자유 함수로 적지만, 이미 병합된 같은 계층의
두 스케줄러(`ExecutionLoopScheduler`, `LedgerIntegrityScheduler`)가 전부
클래스 + `run_forever()` 메서드 패턴이라 그쪽을 따른다(main.py 배선 지점이
이미 그 패턴을 전제로 만들어져 있다 — task-712 decision).

ingest 부분은 이 리프 범위에 "어떤 심볼을 주기적으로 수집할지" 결정하는
포트·설정이 없어(LA-9 포트 5개에 없음, config에도 없음) 호출자가 명시적으로
넘기는 `watched: Sequence[WatchedSeries]`로만 동작한다(기본값 빈 시퀀스 —
품질 지표 export만 수행). 운영 심볼 목록·자격증명 배선은 후속(§10, 미확정).
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
    """스케줄러가 매 주기 다시 fetch할 (venue, 심볼, timeframe). `lookback`은
    매 주기 `[now - lookback, now)`를 다시 수집한다 — `md_candle`은 `ON
    CONFLICT DO NOTHING`(§5)이라 겹치는 범위를 반복 수집해도 안전하다."""

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
        assert self._source is not None and self._audit is not None  # __init__ 불변조건
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
        """한 주기: 감시 대상 ingest(항목별 실패 격리) → 품질 게이지 export.

        ingest 실패는 이 주기의 export를 막지 않는다 — export는 이미 저장된
        배치를 훑을 뿐이라 이번 주기에 새로 들어오지 못한 데이터가 있어도
        그 자체로 STALE 게이지에 반영된다(§4.1)."""
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
        """main.py 백그라운드 태스크 본체. 한 주기 전체 실패가 루프를 죽이지
        않는다(execution_loop/scheduler.py와 동일 설계)."""
        while True:
            await asyncio.sleep(self.interval_seconds)
            try:
                await self.run_once()
            except Exception:
                logger.exception("market_data_scheduler: 이번 주기 전체 실패 — 다음 주기에 재시도")
