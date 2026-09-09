"""DC-16 — market-data 백필 잡: 갭 계획 → 원천 조회 → 저장 → 커버리지 갱신.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§9 DC-16(선행 DC-7=task-1178, DC-11=task-1187, DC-13=task-1212 전부 done).

이 모듈은 세 갈래 책임을 오케스트레이션만 한다(§C 중복 컨텍스트 금지 —
알고리즘을 다시 만들지 않는다): 갭 계산은 `domain/coverage/gaps.plan_fetch`
(DC-7), 원천 조회는 `ports/provider.MarketDataProvider`(DC-5/11 SPI —
구체 벤더 어댑터는 이 파일이 알지 못한다), 저장은 `ports/candle_store.
CandleStore`(LA-9 포트 — `adapters/storage/hot_postgres.HotPostgresStorage`가
DC-13에서 이미 구현), 커버리지 병합은 `domain/coverage/registry.merge_spans`
(DC-6)에 위임한다.

경계 1 — `CoverageSpan` 이중 계약(정직하게 남겨 둔다, `adapters/
postgres_coverage_repository.py`·`application/get_coverage.py`가 이미 문서화한
편차 그대로): 저장 포트(`ports/coverage_repository.CoverageSpan` — 2단계
quality, asset_class 없음)와 병합 도메인(`contracts/v2/coverage.CoverageSpan`
— 3단계 quality_grade, asset_class 필수)이 코드베이스에 공존한다. 이 모듈은
`get_coverage.py`(task-2195)가 쓴 것과 같은 매핑으로 왕복 변환한다(그
매핑은 private 상수라 재import하지 않고 동일 내용을 이 파일에도 둔다).

경계 2 — `instrument_id` 네임스페이스(hot_postgres.py가 이미 문서화):
`CandleStore`/`SeriesKey`는 `md_instrument`(UUID) 키를, `CoverageSpan`/
`VenueListing`은 DC-1 `instruments`(ULID) 키를 쓴다. 두 값을 잇는 매핑은
이 리프 범위 밖(hot_postgres.py decision과 동일) — 호출자가 이미 둘 다
안다고 전제하고 `series_key`(md_instrument 축)와 `listing`(ULID 축 —
`listing.instrument_id`가 커버리지 질의 키)을 각각 받는다. 둘의 `venue`가
다르면 서로 다른 시계열을 하나로 착각한 요청이므로 fail-closed 거부한다.

중단 후 재개 — 이 함수는 자체 트랜잭션을 열지 않는다(LA-9 포트 docstring:
트랜잭션 경계는 호출자 소관). 갭마다 `store.upsert_batch` → `coverage_repo.
upsert_span` 순서로 실행하고, 호출자가 명시적 트랜잭션으로 감싸지 않는 한
각 SQL은 즉시 커밋된다 — 갭 N에서 예외가 나도 갭 1..N-1은 이미 영속화돼
있다. 이 함수를 다시 호출하면 `list_spans`·`query`가 최신 DB 상태를 읽어
`plan_fetch`가 이미 채워진 구간을 다시 갭으로 잡지 않는다 — 그 자체가
재개이고, 별도 체크포인트 테이블이 필요 없다.

조용한 0 채움 금지(§4.1) — provider가 어떤 갭에 캔들을 하나도 돌려주지
않으면(진짜 데이터 부재, 오류 아님) 그 갭은 `stored=0`·`span=None`으로
남기고 커버리지 span을 만들지 않는다. 다음 실행에서 `plan_fetch`가 같은
구간을 다시 갭으로 보고하므로, "빈 응답 = 커버됨"으로 조용히 넘어가지
않는다.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

import asyncpg

from src.data.models.base import AssetClass
from src.foundation.market_data.contracts.v1 import CandleRecord, SeriesKey, Timeframe
from src.foundation.market_data.contracts.v2.coverage import CoverageSpan as MergeCoverageSpan
from src.foundation.market_data.contracts.v2.coverage import QualityGrade
from src.foundation.market_data.contracts.v2.instruments import VenueListing
from src.foundation.market_data.domain.calendar.session_rules import VenueCalendar
from src.foundation.market_data.domain.candle_columns import CandleColumns, to_candle_records
from src.foundation.market_data.domain.coverage.gaps import CoverageGap, plan_fetch
from src.foundation.market_data.domain.coverage.registry import merge_spans
from src.foundation.market_data.domain.timeframe import duration
from src.foundation.market_data.ports.candle_store import CandleStore
from src.foundation.market_data.ports.coverage_repository import CoverageQuality, CoverageRepository
from src.foundation.market_data.ports.coverage_repository import CoverageSpan as StoredCoverageSpan
from src.foundation.market_data.ports.provider import MarketDataProvider, TimeSpan

__all__ = ["BackfillSegmentResult", "BackfillJobResult", "VenueMismatchError", "run_backfill_job"]

_QUALITY_TO_GRADE = {
    CoverageQuality.PROVISIONAL: QualityGrade.RAW,
    CoverageQuality.VALIDATED: QualityGrade.VALIDATED,
}


class VenueMismatchError(ValueError):
    """`listing.venue`와 `series_key.venue`가 다름 — 두 축이 어긋난 요청은
    서로 다른 시계열을 하나로 착각한 것이므로 fail-closed 거부한다."""


def _to_merge_span(span: StoredCoverageSpan, *, asset_class: AssetClass) -> MergeCoverageSpan:
    return MergeCoverageSpan(
        instrument_id=span.instrument_id,
        venue=span.venue,
        asset_class=asset_class,
        timeframe=span.timeframe,
        quality_grade=_QUALITY_TO_GRADE[span.quality],
        start_at=span.start,
        end_at=span.end,
    )


def _validated_candles(columns: CandleColumns, key: SeriesKey) -> list[CandleRecord]:
    """provider가 갓 반환한(아직 `ohlc_sanity.check_candle`을 거치지 않은)
    컬럼 배열을 `CandleRecord`로 변환한다. `to_candle_records`의 길이 가드·
    `close_time` 유도는 그대로 재사용하되(재구현 금지), 그 함수가 반환하는
    인스턴스는 `model_construct`로 검증을 건너뛴 것이라 저장 전 신뢰할 수
    없는 원천 데이터에는 안전하지 않다(그 함수 docstring: "쓰기 시점에
    check_candle을 통과한 데이터에만 안전") — 그래서 각 레코드를 다시
    pydantic 생성자에 통과시켜 타입·형식을 검증한다. OHLC 부호·스파이크 등
    의미론적 품질 게이트는 이 리프 범위 밖이다(LA-15 `ingest_candles`가
    실시간 경로에서 담당)."""
    unchecked = to_candle_records(columns, key)
    return [CandleRecord.model_validate(c.model_dump()) for c in unchecked]


@dataclass(frozen=True, slots=True)
class BackfillSegmentResult:
    """갭 하나를 처리한 결과. `stored=0`은 provider가 그 구간에 캔들이
    없다고 답한 경우다(오류 아님) — 이때 `span`은 `None`이고, 커버리지
    span도 만들지 않는다(§4.1 조용한 0 채움 금지)."""

    gap: CoverageGap
    stored: int
    span: StoredCoverageSpan | None


@dataclass(frozen=True, slots=True)
class BackfillJobResult:
    gaps_planned: int
    segments: list[BackfillSegmentResult]
    merged_coverage: list[MergeCoverageSpan]


async def run_backfill_job(
    *,
    conn: asyncpg.Connection,
    provider: MarketDataProvider,
    store: CandleStore,
    coverage_repo: CoverageRepository,
    listing: VenueListing,
    series_key: SeriesKey,
    tf: Timeframe,
    calendar: VenueCalendar,
    asset_class: AssetClass,
    quality: CoverageQuality,
    range_start: datetime,
    range_end: datetime,
) -> BackfillJobResult:
    """`[range_start, range_end)`에서 `plan_fetch`(DC-7)가 찾은 갭마다
    `provider.fetch_candles`로 채우고, `store.upsert_batch`로 저장한 뒤
    `coverage_repo.upsert_span`으로 커버리지를 갱신한다. 갭은 이미
    `plan_fetch`가 서로 겹치지 않게 정렬해 돌려주므로, 갭끼리는 순서대로
    처리하면 된다(재계획 없이 한 번의 `plan_fetch` 결과를 그대로 순회)."""
    if listing.venue is not series_key.venue:
        raise VenueMismatchError(
            f"listing.venue({listing.venue.value}) != series_key.venue"
            f"({series_key.venue.value})"
        )

    stored_spans = [
        span
        for span in await coverage_repo.list_spans(conn, listing.instrument_id, tf)
        if span.venue is listing.venue
    ]
    existing_candles = await store.query(conn, series_key, range_start, range_end, as_of=None)

    gaps = plan_fetch(
        spans=[_to_merge_span(s, asset_class=asset_class) for s in stored_spans],
        candles=existing_candles,
        tf=tf,
        calendar=calendar,
        range_start=range_start,
        range_end=range_end,
    )

    merged = [_to_merge_span(s, asset_class=asset_class) for s in stored_spans]
    segments: list[BackfillSegmentResult] = []
    for gap in gaps:
        columns = await provider.fetch_candles(
            listing, tf, TimeSpan(start=gap.start_at, end=gap.end_at)
        )
        candles = _validated_candles(columns, series_key)
        if not candles:
            segments.append(BackfillSegmentResult(gap=gap, stored=0, span=None))
            continue

        stored_count = await store.upsert_batch(conn, uuid4(), candles)
        new_span = StoredCoverageSpan(
            instrument_id=listing.instrument_id,
            venue=listing.venue,
            timeframe=tf,
            quality=quality,
            start=candles[0].open_time,
            end=candles[-1].open_time + duration(tf),
        )
        saved_span = await coverage_repo.upsert_span(conn, new_span)
        merged = merge_spans([*merged, _to_merge_span(saved_span, asset_class=asset_class)])
        segments.append(BackfillSegmentResult(gap=gap, stored=stored_count, span=saved_span))

    return BackfillJobResult(gaps_planned=len(gaps), segments=segments, merged_coverage=merged)
