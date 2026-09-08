"""DC-16 — 갭 계획→백필→커버리지 갱신, 중단 후 재개.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§2.1 DC-16, §9.2 DC-16(선행 DC-7=task-1178·DC-11=task-1187·DC-13=task-1212
전부 done).

이 잡은 새 갭 산식을 만들지 않는다(§C 중복 컨텍스트 금지) — 갭 계획은
`domain/coverage/gaps.plan_fetch`(DC-7) 호출뿐이다. 원천 조회는 구체
벤더 어댑터(bitget/kis)를 임포트하지 않고 `ports/provider.MarketDataProvider`
SPI(DC-11 `base_adapter.py`가 그 위에 얹는 공통 배관)로만 받는다. 저장은
`ports/candle_store.CandleStore` 포트에 위임한다 — 실제 구현은
`adapters/storage/hot_postgres.py`(DC-13)가 `PostgresCandleStore` 위에
제공하는 얇은 파사드다. 구간 하나를 저장할 때마다 `domain/coverage/
registry.merge_spans`(DC-6)로 커버리지 스팬을 갱신한다 — 병합 산식은
재구현하지 않고 그대로 재사용한다.

새 테이블·마이그레이션은 신설하지 않는다(decision). 커버리지 스팬의 영속
저장은 이 파일이 정의하는 `CoverageSource` 포트(읽기/기록 두 메서드뿐)로
호출자에게 위임한다 — 병합 산식 자체는 여전히 `registry.merge_spans`
소관이라 이 포트는 저장 배관만 제공하고 갭 판정 로직을 갖지 않는다.

두 인스트루먼트 id 네임스페이스가 섞인다(`hot_postgres.py`가 이미 flag한
needs_decision과 동일 경계): `CoverageSpan`(DC-6/contracts/v2)은 DC 네임스페이스
ULID를, `CandleStore`/`SeriesKey`(LA-1)는 LA 네임스페이스 UUID를 쓴다 — 이
잡은 두 값을 각각 명시적으로 받고(`coverage_instrument_id`/`series_instrument_id`)
서로 변환하지 않는다.

재개 멱등(DoD (a)): 한 구간을 저장한 직후 바로 그 구간의 커버리지 스팬을
기록하므로, 중간에 프로세스가 죽어도 다음 실행은 `plan_fetch`가 이미
저장된 candles·이미 기록된 span만큼을 다시 갭으로 보고하지 않는다 —
전체 요청 구간이 이미 커버됐다면 gaps가 빈 리스트가 되어 provider를
아예 호출하지 않는다(2회차 fetch 호출 횟수 0).
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable
from uuid import UUID, uuid4

import asyncpg

from src.data.models.base import AssetClass
from src.foundation.market_data.contracts.v1 import CandleRecord, SeriesKey, Timeframe, Venue
from src.foundation.market_data.contracts.v2.coverage import CoverageSpan, QualityGrade
from src.foundation.market_data.contracts.v2.instruments import ULID, VenueListing
from src.foundation.market_data.domain.calendar.session_rules import VenueCalendar
from src.foundation.market_data.domain.candle_columns import to_candle_records
from src.foundation.market_data.domain.coverage.gaps import CoverageGap, plan_fetch
from src.foundation.market_data.domain.coverage.registry import merge_spans
from src.foundation.market_data.ports.candle_store import CandleStore
from src.foundation.market_data.ports.provider import MarketDataProvider, TimeSpan

__all__ = [
    "BackfillRequest",
    "BackfillReport",
    "BackfillSegmentResult",
    "CoverageSource",
    "run_backfill",
]


@runtime_checkable
class CoverageSource(Protocol):
    """커버리지 스팬 저장 배관(이 리프 신설, 새 테이블·마이그레이션 없음).

    병합 산식(겹침·인접 처리)은 여기 없다 — `registry.merge_spans`가 이미
    병합한 최종 목록을 그대로 기록만 한다."""

    async def spans_for(self, instrument_id: str, tf: Timeframe) -> list[CoverageSpan]:
        """`(instrument_id, tf)` 축의 현재 알려진 커버리지 스팬. 없으면 빈
        리스트(§4.1 "커버리지 밖 구간을 0으로 채우지 말라"와 같은 의미론 —
        빈 리스트는 그 자체로 "선언 없음"이다)."""
        ...

    async def record_spans(self, spans: Sequence[CoverageSpan]) -> None:
        """`merge_spans`가 이미 병합한 `(instrument_id, tf)` 축의 최종 목록을
        그대로 대체 기록한다(호출자가 넘긴 목록이 SSOT — 이 메서드 자체는
        추가 병합을 하지 않는다)."""
        ...


@dataclass(frozen=True)
class BackfillRequest:
    """한 번의 백필 실행 입력. `coverage_instrument_id`(DC ULID)는 갭 계획·
    커버리지 갱신에, `series_instrument_id`(LA UUID)는 `CandleStore` 조회·
    저장에 각각 쓰인다(모듈 docstring의 네임스페이스 경계 그대로)."""

    coverage_instrument_id: ULID
    series_instrument_id: UUID
    venue: Venue
    asset_class: AssetClass
    quality_grade: QualityGrade
    tf: Timeframe
    listing: VenueListing
    calendar: VenueCalendar
    range_start: datetime
    range_end: datetime


@dataclass
class BackfillSegmentResult:
    gap: CoverageGap
    stored_count: int


@dataclass
class BackfillReport:
    gaps_planned: list[CoverageGap] = field(default_factory=list)
    segments: list[BackfillSegmentResult] = field(default_factory=list)

    @property
    def fetched_segments(self) -> int:
        return len(self.segments)


async def run_backfill(
    request: BackfillRequest,
    *,
    provider: MarketDataProvider,
    store: CandleStore,
    coverage: CoverageSource,
    conn: asyncpg.Connection,
) -> BackfillReport:
    """§9 DC-16 DoD 본체: 갭 계획(DC-7) → 구간별 백필(DC-11 SPI) → 저장
    (DC-13 `CandleStore` 포트) → 커버리지 갱신(DC-6 `merge_spans`), 구간
    하나가 끝날 때마다 커버리지를 갱신해 중단 후 재개가 안전하다."""
    key = SeriesKey(
        venue=request.venue,
        instrument_id=request.series_instrument_id,
        timeframe=request.tf,
    )
    known_spans = await coverage.spans_for(request.coverage_instrument_id, request.tf)
    existing_candles = await store.query(
        conn, key, request.range_start, request.range_end, as_of=None
    )
    gaps = plan_fetch(
        spans=known_spans,
        candles=existing_candles,
        tf=request.tf,
        calendar=request.calendar,
        range_start=request.range_start,
        range_end=request.range_end,
    )

    report = BackfillReport(gaps_planned=gaps)
    for gap in gaps:
        columns = await provider.fetch_candles(
            request.listing, request.tf, TimeSpan(start=gap.start_at, end=gap.end_at)
        )
        candles: list[CandleRecord] = to_candle_records(columns, key)
        stored_count = await store.upsert_batch(conn, uuid4(), candles)

        new_span = CoverageSpan(
            instrument_id=request.coverage_instrument_id,
            venue=request.venue,
            asset_class=request.asset_class,
            timeframe=request.tf,
            quality_grade=request.quality_grade,
            start_at=gap.start_at,
            end_at=gap.end_at,
        )
        known_spans = merge_spans([*known_spans, new_span])
        await coverage.record_spans(known_spans)

        report.segments.append(BackfillSegmentResult(gap=gap, stored_count=stored_count))

    return report
