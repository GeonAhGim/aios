"""DC-16 — 커버리지 갭을 순서대로 백필하고 커버리지를 갱신한다.

갭의 의미는 DC-7에만 위임한다. 이 유스케이스는 provider와 저장 포트 사이의
배선만 담당하며, 각 갭을 성공적으로 저장한 뒤에만 그 구간을 커버리지로
선언한다. 따라서 중간 실패 후 같은 입력으로 다시 실행해도 완료된 구간은
다시 provider에 요청하지 않는다.
"""
from __future__ import annotations

from collections.abc import Callable, MutableSequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from src.data.models.base import AssetClass
from src.foundation.market_data.contracts.v1 import CandleRecord, SeriesKey
from src.foundation.market_data.contracts.v2.coverage import CoverageSpan, QualityGrade
from src.foundation.market_data.contracts.v2.instruments import VenueListing
from src.foundation.market_data.domain.calendar.session_rules import VenueCalendar
from src.foundation.market_data.domain.candle_columns import to_candle_records
from src.foundation.market_data.domain.coverage.gaps import CoverageGap, plan_fetch
from src.foundation.market_data.domain.coverage.registry import merge_spans
from src.foundation.market_data.ports.candle_store import CandleStore
from src.foundation.market_data.ports.provider import MarketDataProvider, TimeSpan

__all__ = ["BackfillRequest", "BackfillResult", "backfill"]


@dataclass(frozen=True)
class BackfillRequest:
    listing: VenueListing
    key: SeriesKey
    asset_class: AssetClass
    quality_grade: QualityGrade
    calendar: VenueCalendar
    range_start: datetime
    range_end: datetime


@dataclass(frozen=True)
class BackfillResult:
    fetched_gaps: tuple[CoverageGap, ...]
    coverage_spans: tuple[CoverageSpan, ...]
    candles: tuple[CandleRecord, ...]


async def backfill(
    request: BackfillRequest,
    *,
    provider: MarketDataProvider,
    store: CandleStore,
    coverage_spans: MutableSequence[CoverageSpan],
    candles: MutableSequence[CandleRecord],
    conn: object = None,
    batch_id_factory: Callable[[], UUID] = uuid4,
) -> BackfillResult:
    """계획된 갭을 하나씩 fetch → 저장 → 커버리지 갱신한다.

    ``coverage_spans``와 ``candles``는 호출자가 가진 최신 상태다. 성공한
    구간은 두 시퀀스에 즉시 반영되므로 provider가 이후 구간에서 실패해도
    앞선 구간의 진행 상태를 잃지 않는다.
    """
    gaps = plan_fetch(
        spans=coverage_spans,
        candles=candles,
        tf=request.key.timeframe,
        calendar=request.calendar,
        range_start=request.range_start,
        range_end=request.range_end,
    )

    fetched: list[CoverageGap] = []
    for gap in gaps:
        columns = await provider.fetch_candles(
            request.listing,
            request.key.timeframe,
            TimeSpan(start=gap.start_at, end=gap.end_at),
        )
        fetched_candles = to_candle_records(columns, request.key)
        await store.upsert_batch(conn, batch_id_factory(), fetched_candles)
        candles.extend(fetched_candles)

        updated = CoverageSpan(
            instrument_id=request.listing.instrument_id,
            venue=request.listing.venue,
            asset_class=request.asset_class,
            timeframe=request.key.timeframe,
            quality_grade=request.quality_grade,
            start_at=gap.start_at,
            end_at=gap.end_at,
        )
        coverage_spans[:] = merge_spans([*coverage_spans, updated])
        fetched.append(gap)

    return BackfillResult(tuple(fetched), tuple(coverage_spans), tuple(candles))
