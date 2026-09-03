"""DC-6 — 커버리지 선언 질의·병합(순수).

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§2.1 DC-6, §4.1(fail-closed, `coverage_spans` 겹침 금지 EXCLUDE 제약과
동일 의미론), §9.2 DC-6.

커버리지 선언은 (벤처×자산군×TF×기간×품질등급) 축이다
(`CoverageSpan`, contracts/v2/coverage.py — task-1127 decision: DC-7
`gaps.plan_fetch`가 이 반환 타입에 의존하므로 domain이 아니라 contracts에
둔다). 이 모듈은 그 선언들을 instrument×timeframe으로 질의하고, 같은
(instrument_id, venue, asset_class, timeframe, quality_grade) 축 안에서
겹치거나 경계가 맞닿은(adjacent) span을 하나로 병합한다. 축이 다르면
(venue가 다르거나 quality_grade가 다르면) 같은 기간이 겹쳐도 병합하지
않는다 — 서로 다른 소스·품질의 독립적 선언이기 때문이다.

병합 결과에 겹침이 남으면 DB EXCLUDE 제약(§4.1)이 거부할 데이터이므로
반드시 없어야 한다 — `merge_spans`가 그 불변조건을 코드로 증명한다.
경계가 정확히 맞닿은 두 span(`a.end_at == b.start_at`)은 연속 구간이므로
병합하고, 사이에 간격이 있는 두 span은 불연속이므로 별개로 남긴다.

저장소 조회·I/O는 없다 — 호출자(application/adapters)가 이미 읽은
`CoverageSpan` 목록을 넘긴다. 커버리지 밖 구간을 0/NaN으로 채우는 것은
이 모듈의 책임이 아니다(§4.1) — DC-7 `gaps.py`가 그 fail-closed 판정을
이어받는다.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

from src.foundation.market_data.contracts.v1 import Timeframe
from src.foundation.market_data.contracts.v2.coverage import CoverageSpan
from src.foundation.market_data.contracts.v2.instruments import Instrument

__all__ = ["merge_spans", "coverage_for"]

_AxisKey = tuple[str, str, str, str, str]


def _axis_key(span: CoverageSpan) -> _AxisKey:
    return (
        span.instrument_id,
        span.venue.value,
        span.asset_class.value,
        span.timeframe.value,
        span.quality_grade.value,
    )


def merge_spans(spans: Sequence[CoverageSpan]) -> list[CoverageSpan]:
    """같은 축 안에서 겹치거나 경계가 맞닿은 span을 결정론적으로 병합한다.

    각 축 그룹을 `(start_at, end_at)` 오름차순으로 정렬한 뒤 스캔한다 —
    입력 순서와 무관하게 항상 같은 결과가 나온다(결정론). 반환 순서는
    축 키 오름차순 → 그 안에서 `start_at` 오름차순으로 고정된다.
    """
    groups: dict[_AxisKey, list[CoverageSpan]] = defaultdict(list)
    for span in spans:
        groups[_axis_key(span)].append(span)

    merged: list[CoverageSpan] = []
    for key in sorted(groups):
        ordered = sorted(groups[key], key=lambda s: (s.start_at, s.end_at))
        current = ordered[0]
        for candidate in ordered[1:]:
            if candidate.start_at <= current.end_at:
                # 겹침(start < 현재 end) 또는 경계 맞닿음(start == 현재 end) — 병합.
                if candidate.end_at > current.end_at:
                    current = current.model_copy(update={"end_at": candidate.end_at})
            else:
                # 사이 간격이 있는 불연속 span — 별개로 남긴다.
                merged.append(current)
                current = candidate
        merged.append(current)
    return merged


def coverage_for(
    spans: Sequence[CoverageSpan], instrument: Instrument, tf: Timeframe
) -> list[CoverageSpan]:
    """`instrument`×`tf`에 해당하는 커버리지 선언을 질의·병합해 반환한다.

    `spans`는 임의 instrument·timeframe이 섞인 원본 선언 목록일 수 있다
    — 먼저 `instrument.instrument_id`와 `tf`로 필터링한 뒤 `merge_spans`로
    병합한다. 일치하는 선언이 없으면 빈 리스트(커버리지 없음 — 호출자가
    `DATA_COVERAGE_MISSING`으로 판정할 근거).
    """
    matching = [
        span
        for span in spans
        if span.instrument_id == instrument.instrument_id and span.timeframe == tf
    ]
    return merge_spans(matching)
