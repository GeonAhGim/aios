"""DC-16 — 커버리지 갭을 채우는 재개 가능한 백필 유스케이스.

갭 판정은 DC-7에, 원천 조회는 DC-5 SPI에, 저장은 LA-9 포트에 위임한다.
이 모듈은 저장소나 벤더 어댑터를 직접 알지 않으며, 각 갭을 저장하고
커버리지 레지스트리를 갱신하는 순서만 조정한다.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

from src.data.models.base import AssetClass
from src.foundation.market_data.contracts.v1 import SeriesKey
from src.foundation.market_data.contracts.v2.coverage import CoverageSpan, QualityGrade
from src.foundation.market_data.contracts.v2.instruments import VenueListing
from src.foundation.market_data.domain.calendar.session_rules import VenueCalendar
from src.foundation.market_data.domain.candle_columns import to_candle_records
from src.foundation.market_data.domain.coverage.gaps import plan_fetch
from src.foundation.market_data.domain.coverage.registry import merge_spans
from src.foundation.market_data.ports.candle_store import CandleStore
from src.foundation.market_data.ports.provider import MarketDataProvider, TimeSpan

__all__ = ["BackfillJob", "BackfillResult", "run_backfill"]


@dataclass(frozen=True)
class BackfillResult:
    gaps_planned: int
    gaps_filled: int
    candles_written: int


class BackfillJob:
    """한 시계열의 백필을 수행한다.

    ``coverage_spans``는 호출자가 관리하는 현재 커버리지 스냅샷이다. 갭 하나가
    성공할 때마다 병합된 새 스냅샷으로 교체되므로, 예외로 중단되어도 이미
    완료한 구간은 다음 ``run``에서 다시 조회되지 않는다.
    """

    def __init__(
        self,
        *,
        provider: MarketDataProvider,
        store: CandleStore,
        listing: VenueListing,
        series_key: SeriesKey,
        asset_class: AssetClass,
        calendar: VenueCalendar,
        coverage_spans: Sequence[CoverageSpan] = (),
        quality_grade: QualityGrade = QualityGrade.VALIDATED,
        conn: object | None = None,
    ) -> None:
        self._provider = provider
        self._store = store
        self._listing = listing
        self._series_key = series_key
        self._asset_class = asset_class
        self._calendar = calendar
        self._coverage_spans = list(coverage_spans)
        self._quality_grade = quality_grade
        self._conn = conn

    @property
    def coverage_spans(self) -> list[CoverageSpan]:
        return list(self._coverage_spans)

    async def run(self, range_start: datetime, range_end: datetime) -> BackfillResult:
        candles = await self._store.query(
            self._conn, self._series_key, range_start, range_end, None
        )
        gaps = plan_fetch(
            spans=self._coverage_spans,
            candles=candles,
            tf=self._series_key.timeframe,
            calendar=self._calendar,
            range_start=range_start,
            range_end=range_end,
        )
        written = 0
        filled = 0
        for gap in gaps:
            batch = await self._provider.fetch_candles(
                self._listing,
                self._series_key.timeframe,
                TimeSpan(start=gap.start_at, end=gap.end_at),
            )
            fetched = to_candle_records(batch, self._series_key)
            if not fetched:
                continue
            written += await self._store.upsert_batch(self._conn, uuid4(), fetched)
            self._coverage_spans = merge_spans(
                [
                    *self._coverage_spans,
                    CoverageSpan(
                        instrument_id=self._listing.instrument_id,
                        venue=self._listing.venue,
                        asset_class=self._asset_class,
                        timeframe=self._series_key.timeframe,
                        quality_grade=self._quality_grade,
                        start_at=gap.start_at,
                        end_at=gap.end_at,
                    ),
                ]
            )
            filled += 1
            candles.extend(fetched)
        return BackfillResult(len(gaps), filled, written)


async def run_backfill(
    *,
    provider: MarketDataProvider,
    store: CandleStore,
    listing: VenueListing,
    series_key: SeriesKey,
    asset_class: AssetClass,
    calendar: VenueCalendar,
    range_start: datetime,
    range_end: datetime,
    coverage_spans: Sequence[CoverageSpan] = (),
    quality_grade: QualityGrade = QualityGrade.VALIDATED,
    conn: object | None = None,
) -> BackfillResult:
    """Convenience wrapper for callers that do not need a long-lived job object."""
    return await BackfillJob(
        provider=provider,
        store=store,
        listing=listing,
        series_key=series_key,
        asset_class=asset_class,
        calendar=calendar,
        coverage_spans=coverage_spans,
        quality_grade=quality_grade,
        conn=conn,
    ).run(range_start, range_end)
