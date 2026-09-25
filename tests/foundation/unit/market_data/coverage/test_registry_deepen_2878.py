"""DC-6 domain/coverage/registry.py — DEEPEN(task-2878, task-1127 D1 -> D3 증빙.

기존 test_registry.py는 `merge_spans`/`coverage_for`의 핵심 의미론(겹침·인접
병합, 축 분리, 순서 무관성)을 17개 테스트로 증명한다(D1/D2 수준). 이 파일은
동일 패턴(task-2875 `test_lifecycle_deepen_2875.py`)을 따라 그 부족분을
채운다 — 새 기능은 추가하지 않는다.

1. 실패 주입 — 타입힌트(Sequence[CoverageSpan])는 런타임을 강제하지 않는
   요소(비-CoverageSpan, None)와, 대량 중복 선언 플러딩을 주입해도 조용히
   틀린 결과를 내지 않고 항상 즉시 크래시하거나(fail-closed) 불변조건을
   유지함을 증명한다.
2. 성능 단언 — 다축·대량 span 병합이 절대시간 예산 내에 있다(회귀가
   있다면 O(n^2) 등으로의 퇴화다).
3. 게이트 적색 재현 — 실제 커버리지 선언 파이프라인(소스가 구간을 순차
   선언)을 재생하며, `CoverageSpan` 자체 불변조건(§4.1)이 적색(거부)을
   내는 시도가 있어도 그 직전까지 누적된 선언의 병합 결과는 오염되지
   않음을 증명한다.
4. 리플레이 결정론 + 동시 다중 인스턴스(D3) — 무작위(고정 시드) 입력에서도
   병합 불변조건이 항상 성립하고(fuzz), 같은 입력 재호출이 항상 같은
   결과이며, 여러 스레드가 서로 다른 축을 동시에 병합해도 서로 오염시키지
   않는다(모듈 전역 가변 상태 없음의 동시성 증거).
"""

from __future__ import annotations

import random
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, cast

import pytest
from pydantic import ValidationError

from src.data.models.base import AssetClass
from src.foundation.market_data.contracts.v1 import Timeframe, Venue
from src.foundation.market_data.contracts.v2.coverage import CoverageSpan, QualityGrade
from src.foundation.market_data.contracts.v2.instruments import Instrument, InstrumentLifecycle
from src.foundation.market_data.domain.coverage.registry import coverage_for, merge_spans
from tests.conftest import PerfBudget

_VALID_ULID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"


def _dt(day: int, hour: int = 0) -> datetime:
    return datetime(2026, 1, day, hour, 0, tzinfo=timezone.utc)


def _span(
    *,
    instrument_id: str = _VALID_ULID,
    venue: Venue = Venue.BITGET,
    asset_class: AssetClass = AssetClass.CRYPTO,
    timeframe: Timeframe = Timeframe.D1,
    quality_grade: QualityGrade = QualityGrade.RAW,
    start_at: datetime,
    end_at: datetime,
) -> CoverageSpan:
    return CoverageSpan(
        instrument_id=instrument_id,
        venue=venue,
        asset_class=asset_class,
        timeframe=timeframe,
        quality_grade=quality_grade,
        start_at=start_at,
        end_at=end_at,
    )


def _instrument(instrument_id: str = _VALID_ULID) -> Instrument:
    return Instrument(
        instrument_id=instrument_id,
        asset_class=AssetClass.CRYPTO,
        base="BTC",
        quote="USDT",
        isin=None,
        figi=None,
        tick_size=Decimal("0.01"),
        lot_size=Decimal("0.0001"),
        calendar_id="24x7",
        lifecycle_state=InstrumentLifecycle.ACTIVE,
        created_at=_dt(1),
    )


# ---- 실패 주입(D2) — CoverageSpan 계약 경계(negative, >=5) ----


def test_coverage_span_rejects_naive_start_at() -> None:
    """§4.1은 tz-aware UTC를 요구한다 — naive datetime은 절대시각이 아니라
    거부돼야 한다."""
    with pytest.raises(ValidationError):
        _span(start_at=datetime(2026, 1, 1), end_at=_dt(2))


def test_coverage_span_rejects_naive_end_at() -> None:
    with pytest.raises(ValidationError):
        _span(start_at=_dt(1), end_at=datetime(2026, 1, 2))


def test_coverage_span_rejects_invalid_ulid_instrument_id() -> None:
    with pytest.raises(ValidationError):
        _span(instrument_id="not-a-valid-ulid", start_at=_dt(1), end_at=_dt(2))


def test_coverage_span_rejects_ulid_with_crockford_excluded_characters() -> None:
    """Crockford Base32는 I/L/O/U를 제외한다 — 26자 형식이어도 이 문자가
    섞이면 거부돼야 한다."""
    garbage = "0IARZ3NDEKTSV4RRFFQ69G5FA0"  # 26자, 'I'는 Crockford에서 제외된 문자
    with pytest.raises(ValidationError):
        _span(instrument_id=garbage, start_at=_dt(1), end_at=_dt(2))


def test_coverage_span_rejects_unknown_quality_grade() -> None:
    with pytest.raises(ValidationError):
        _span(quality_grade=cast(Any, "PLATINUM"), start_at=_dt(1), end_at=_dt(2))


def test_coverage_span_is_frozen_and_rejects_mutation() -> None:
    """`frozen=True` — 생성 후 변경 시도는 항상 거부돼야 한다. 이 모듈의
    병합 불변조건(§4.1 EXCLUDE 제약 동일 의미론)은 span이 불변임을
    전제로 한다 — 변경 가능하면 `merge_spans`가 반환한 결과를 호출자가
    나중에 조작해 겹침을 만들 수 있다."""
    span = _span(start_at=_dt(1), end_at=_dt(2))
    with pytest.raises(ValidationError):
        cast(Any, span).end_at = _dt(5)


# ---- 실패 주입(D2) — merge_spans/coverage_for 호출 경계(failure-injection, 3) ----


def test_merge_spans_rejects_non_coverage_span_element() -> None:
    """`Sequence[CoverageSpan]` 타입힌트는 런타임을 강제하지 않는다 — 역직렬화
    경로 등에서 딕셔너리가 섞여 들어오면 조용히 건너뛰거나 잘못된 결과를
    내지 않고 즉시 크래시해야 한다(fail-closed)."""
    valid = _span(start_at=_dt(1), end_at=_dt(2))
    garbage: Any = {"start_at": _dt(1), "end_at": _dt(2)}
    with pytest.raises(AttributeError):
        merge_spans([valid, garbage])


def test_coverage_for_rejects_none_element_in_spans() -> None:
    instrument = _instrument()
    valid = _span(start_at=_dt(1), end_at=_dt(2))
    with pytest.raises(AttributeError):
        coverage_for(cast(Any, [valid, None]), instrument, Timeframe.D1)


def test_merge_spans_massive_duplicate_flood_stays_within_invariant() -> None:
    """동일 구간을 수백 번 중복 선언하는 플러딩(예: 재시도 폭주로 같은
    선언이 반복 유입)을 주입해도, 병합 결과는 정확히 하나의 span으로
    수렴하고 §4.1 겹침 금지 불변조건이 깨지지 않는다."""
    flood = [_span(start_at=_dt(1), end_at=_dt(2)) for _ in range(500)]
    result = merge_spans(flood)
    assert result == [_span(start_at=_dt(1), end_at=_dt(2))]


# ---- 성능 단언(D2, 1) ----


@pytest.mark.perf
def test_merge_spans_meets_latency_budget_for_large_multi_axis_input(
    perf_budget: PerfBudget,
) -> None:
    """다축(venue x quality_grade) x 대량 span 병합은 정렬+선형 스캔
    (O(n log n))이어야 한다 — 회귀가 있다면 O(n^2) 등으로의 퇴화다."""
    rng = random.Random(2878)
    axes = [
        (venue, grade)
        for venue in (Venue.BITGET, Venue.KIS_KRX, Venue.BINANCE)
        for grade in (QualityGrade.RAW, QualityGrade.VALIDATED, QualityGrade.GOLD)
    ]
    spans: list[CoverageSpan] = []
    for venue, grade in axes:
        for _ in range(1_000):
            start_offset = rng.randint(0, 5_000)
            length = rng.randint(1, 10)
            start = _dt(1) + timedelta(hours=start_offset)
            end = start + timedelta(hours=length)
            spans.append(_span(venue=venue, quality_grade=grade, start_at=start, end_at=end))
    rng.shuffle(spans)

    budget_ms = 2000.0  # 실측 로컬 <0.3s(9축 x 1000 span = 9000개)

    result: list[CoverageSpan] | None = None

    def _run() -> None:
        nonlocal result
        result = merge_spans(spans)

    perf_budget.assert_within(
        _run,
        budget_ms=budget_ms,
        label=f"merge {len(spans)} spans",
    )
    assert result is not None


# ---- 게이트 적색 재현(D2, 1) ----


def test_gate_red_rejected_declaration_mid_replay_does_not_corrupt_prior_merge() -> None:
    """실제 커버리지 선언 파이프라인을 재생한다: 소스가 구간을 순차
    선언하는 도중 하나가 `CoverageSpan` 자체 게이트(§4.1 불변조건, D1
    negative와 동일)에서 적색(거부)돼도, 그 직전까지 누적된 선언들의
    병합·질의 결과는 오염되지 않고 파이프라인은 계속 진행된다."""
    instrument = _instrument()
    declared: list[CoverageSpan] = [
        _span(start_at=_dt(1), end_at=_dt(5)),
        _span(start_at=_dt(5), end_at=_dt(9)),  # 인접 -> 병합 대상
    ]
    before = coverage_for(declared, instrument, Timeframe.D1)
    assert before == [_span(start_at=_dt(1), end_at=_dt(9))]

    # 게이트 적색: 소스가 end_at <= start_at인 불법 구간을 선언하려 시도.
    with pytest.raises(ValidationError):
        _span(start_at=_dt(20), end_at=_dt(15))

    # 거부된 시도는 목록에 추가되지 않았다 — 직전 병합 결과가 그대로 재현된다.
    after_rejection = coverage_for(declared, instrument, Timeframe.D1)
    assert after_rejection == before

    # 정상 선언은 적색 이후에도 계속 이어진다(불연속 span으로 분리 유지).
    declared.append(_span(start_at=_dt(15), end_at=_dt(18)))
    final = coverage_for(declared, instrument, Timeframe.D1)
    assert final == [
        _span(start_at=_dt(1), end_at=_dt(9)),
        _span(start_at=_dt(15), end_at=_dt(18)),
    ]


# ---- 리플레이 결정론 + 퍼즈 + 동시성(D3) ----

_FUZZ_SEEDS = (0, 1, 42, 1337, 2026)


def _random_spans(rng: random.Random, count: int) -> list[CoverageSpan]:
    spans = []
    for _ in range(count):
        start_offset = rng.randint(0, 500)
        length = rng.randint(1, 20)
        start = _dt(1) + timedelta(hours=start_offset)
        end = start + timedelta(hours=length)
        spans.append(_span(start_at=start, end_at=end))
    return spans


@pytest.mark.parametrize("seed", _FUZZ_SEEDS)
def test_fuzz_merge_spans_invariants_hold_for_random_input(seed: int) -> None:
    """고정 시드 5개로 무작위(단일 축) span 묶음을 생성해 `merge_spans`의
    불변조건이 항상 성립함을 증명한다: (a) 결과는 더 이상 겹치거나
    맞닿지 않는 최대 형태이고, (b) 원본의 모든 구간이 결과에 포함되며
    (커버리지를 잃지 않음), (c) 결과의 경계는 원본 경계에서만 온다
    (새 경계를 지어내지 않음)."""
    rng = random.Random(seed)
    spans = _random_spans(rng, 80)
    result = merge_spans(spans)
    ordered = sorted(result, key=lambda s: s.start_at)

    for prev, nxt in zip(ordered, ordered[1:], strict=False):
        assert prev.end_at < nxt.start_at, f"seed={seed}: 병합 후에도 겹침/맞닿음이 남았다"

    for original in spans:
        assert any(
            r.start_at <= original.start_at and original.end_at <= r.end_at for r in ordered
        ), f"seed={seed}: 원본 구간 {original}이 병합 결과에 포함되지 않았다"

    original_starts = {s.start_at for s in spans}
    original_ends = {s.end_at for s in spans}
    for r in ordered:
        assert r.start_at in original_starts, f"seed={seed}: 지어낸 시작 경계 {r.start_at}"
        assert r.end_at in original_ends, f"seed={seed}: 지어낸 종료 경계 {r.end_at}"


def test_replay_merge_spans_and_coverage_for_are_deterministic() -> None:
    """같은 입력을 두 번 호출해도 완전히 동일한 결과 — 숨은 시계·난수·전역
    가변 상태가 없다는 재생(replay) 안전성 증거."""
    rng = random.Random(99)
    spans = _random_spans(rng, 40)
    instrument = _instrument()

    first_merge = merge_spans(spans)
    second_merge = merge_spans(spans)
    assert first_merge == second_merge

    first_query = coverage_for(spans, instrument, Timeframe.D1)
    second_query = coverage_for(spans, instrument, Timeframe.D1)
    assert first_query == second_query


def test_concurrent_merge_calls_across_independent_axes_do_not_cross_contaminate() -> None:
    """서로 다른 축(venue)의 독립적인 span 묶음을 여러 스레드가 동시에
    병합해도 서로의 결과를 오염시키지 않는다 — 모듈 전역 가변 상태가
    없다는 동시성 증거(D3)."""
    venues = (Venue.BITGET, Venue.KIS_KRX, Venue.KIS_US, Venue.BINANCE, Venue.BYBIT, Venue.OKX)

    def _run(venue: Venue) -> tuple[Venue, list[CoverageSpan]]:
        spans = [
            _span(venue=venue, start_at=_dt(1), end_at=_dt(5)),
            _span(venue=venue, start_at=_dt(5), end_at=_dt(9)),  # 인접 -> 병합
            _span(venue=venue, start_at=_dt(20), end_at=_dt(22)),  # 불연속 -> 분리
        ]
        return venue, merge_spans(spans)

    workload = list(venues) * 30  # 180회 동시 호출, 서로 다른 축이 반복 교차
    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(_run, workload))

    assert len(results) == len(workload)
    for venue, result in results:
        expected = [
            _span(venue=venue, start_at=_dt(1), end_at=_dt(9)),
            _span(venue=venue, start_at=_dt(20), end_at=_dt(22)),
        ]
        assert result == expected, f"{venue} 스레드가 다른 축의 병합 결과와 섞였다: {result}"


# ---- 추가 DEEPEN: ULID 검증 경계 + 병합 경계 케이스 ----


@pytest.mark.parametrize(
    "bad_char",
    ["I", "L", "O", "U"],
    ids=["crockford-I", "crockford-L", "crockford-O", "crockford-U"],
)
def test_coverage_span_rejects_each_crockford_excluded_character(bad_char: str) -> None:
    """Crockford Base32에서 제외된 문자(I/L/O/U)가 26자 형식의 어느 위치에
    있든 거부돼야 한다 — 25자 garbage는 길이 검사만으로 거부되므로 Crockford
    문자 검증이 제거되어도 통과하는 허수아비 테스트를 피한다."""
    # 첫 글자(0-7)를 제외한 25자 슬롯 중 하나를 bad_char로 교체
    base = "01ARZ3NDEKTSV4RRFFQ69G5FA"  # 25자 (첫 글자 '0' 제외)
    garbage = "0" + base[:12] + bad_char + base[13:]
    assert len(garbage) == 26, f"garbage 길이가 26이 아님: {len(garbage)}"
    with pytest.raises(ValidationError):
        _span(instrument_id=garbage, start_at=_dt(1), end_at=_dt(2))


def test_coverage_span_normalizes_lowercase_ulid() -> None:
    """ULID validator는 소문자 입력을 .upper()로 정규화한 뒤 패턴을 검사한다 —
    소문자 26자 Crockford Base32는 유효한 ULID으로 수용된다(거부되지 않음)."""
    span = _span(instrument_id="01arz3ndektsv4rrffq69g5fav", start_at=_dt(1), end_at=_dt(2))
    assert span.instrument_id == "01ARZ3NDEKTSV4RRFFQ69G5FAV"


def test_coverage_span_rejects_ulid_with_digit_8_or_9_as_first_char() -> None:
    """ULID 첫 글자는 타임스탬프 오버플로 방지 위해 0-7로 제한된다 —
    8 또는 9로 시작하는 26자는 거부돼야 한다. (XREV task-3649: 이전 버전은
    첫 글자를 교체하며 실수로 한 글자를 통째로 날려 25자 문자열이 됐고,
    길이 검사만으로도 거부돼 첫 글자 제한 자체가 빠져도 통과하는 허수아비
    테스트였다 — 길이를 명시적으로 단언해 재발을 막는다.)"""
    starts_with_8 = "8" + _VALID_ULID[1:]
    starts_with_9 = "9" + _VALID_ULID[1:]
    assert len(starts_with_8) == 26, f"garbage 길이가 26이 아님: {len(starts_with_8)}"
    assert len(starts_with_9) == 26, f"garbage 길이가 26이 아님: {len(starts_with_9)}"
    with pytest.raises(ValidationError):
        _span(instrument_id=starts_with_8, start_at=_dt(1), end_at=_dt(2))
    with pytest.raises(ValidationError):
        _span(instrument_id=starts_with_9, start_at=_dt(1), end_at=_dt(2))


def test_merge_spans_empty_input_returns_empty_list() -> None:
    """빈 입력은 빈 리스트를 반환한다 — 예외가 아니라 정상 경로."""
    assert merge_spans([]) == []


def test_coverage_for_no_matching_spans_returns_empty_list() -> None:
    """instrument·timeframe이 하나도 일치하지 않으면 빈 리스트를 반환한다
    (커버리지 없음 — 호출자가 DATA_COVERAGE_MISSING으로 판정)."""
    other_instrument = _instrument(instrument_id="01ARZ3NDEKTSV4RRFFQ69G5FB0")
    spans = [_span(start_at=_dt(1), end_at=_dt(2))]
    assert coverage_for(spans, other_instrument, Timeframe.D1) == []


def test_coverage_for_filters_by_timeframe() -> None:
    """같은 instrument라도 timeframe이 다르면 필터링되어 빈 결과를 반환한다."""
    instrument = _instrument()
    d1_span = _span(timeframe=Timeframe.D1, start_at=_dt(1), end_at=_dt(2))
    h1_span = _span(timeframe=Timeframe.H1, start_at=_dt(1), end_at=_dt(2))
    assert coverage_for([d1_span, h1_span], instrument, Timeframe.D1) == [d1_span]
    assert coverage_for([d1_span, h1_span], instrument, Timeframe.H1) == [h1_span]


def test_merge_spans_nested_overlap_resolves_to_single_span() -> None:
    """완전히 포함(nested)되는 span — [1,10)이 [2,5)를 포함하면 결과는
    [1,10) 하나다. 부분 겹침이 여러 단계에 걸쳐 연결되면 단일 span으로
    수렴한다."""
    spans = [
        _span(start_at=_dt(1), end_at=_dt(10)),
        _span(start_at=_dt(2), end_at=_dt(5)),  # [1,10)에 포함
        _span(start_at=_dt(3), end_at=_dt(4)),  # [2,5)에 포함
    ]
    result = merge_spans(spans)
    assert result == [_span(start_at=_dt(1), end_at=_dt(10))]


def test_merge_spans_chain_overlap_produces_single_span() -> None:
    """연쇄 겹침: [1,3) ∪ [2,5) ∪ [4,7) — 각 쌍이 겹치지만 첫·마지막은
    직접 겹치지 않는다. 결과는 [1,7) 하나다."""
    spans = [
        _span(start_at=_dt(1), end_at=_dt(3)),
        _span(start_at=_dt(2), end_at=_dt(5)),
        _span(start_at=_dt(4), end_at=_dt(7)),
    ]
    result = merge_spans(spans)
    assert result == [_span(start_at=_dt(1), end_at=_dt(7))]


def test_merge_spans_identical_spans_collapse_to_one() -> None:
    """완전히 동일한 span이 여러 개면 하나로 수렴한다."""
    spans = [_span(start_at=_dt(1), end_at=_dt(5)) for _ in range(10)]
    result = merge_spans(spans)
    assert result == [_span(start_at=_dt(1), end_at=_dt(5))]


def test_merge_spans_disjoint_spans_preserve_order() -> None:
    """서로 불연속인 span은 start_at 오름차순으로 정렬되어 반환된다."""
    spans = [
        _span(start_at=_dt(10), end_at=_dt(12)),
        _span(start_at=_dt(1), end_at=_dt(2)),
        _span(start_at=_dt(5), end_at=_dt(6)),
    ]
    result = merge_spans(spans)
    assert result == [
        _span(start_at=_dt(1), end_at=_dt(2)),
        _span(start_at=_dt(5), end_at=_dt(6)),
        _span(start_at=_dt(10), end_at=_dt(12)),
    ]


def test_merge_spans_different_axes_do_not_merge_even_if_overlapping() -> None:
    """같은 기간이라도 venue가 다르면 다른 축이므로 병합하지 않는다 —
    서로 다른 소스의 독립적 선언이다."""
    bitget = _span(venue=Venue.BITGET, start_at=_dt(1), end_at=_dt(5))
    binance = _span(venue=Venue.BINANCE, start_at=_dt(1), end_at=_dt(5))
    result = merge_spans([bitget, binance])
    assert len(result) == 2
    assert bitget in result
    assert binance in result


def test_merge_spans_different_quality_grades_do_not_merge() -> None:
    """같은 venue라도 quality_grade가 다르면 다른 축이므로 병합하지 않는다."""
    raw = _span(quality_grade=QualityGrade.RAW, start_at=_dt(1), end_at=_dt(5))
    gold = _span(quality_grade=QualityGrade.GOLD, start_at=_dt(1), end_at=_dt(5))
    result = merge_spans([raw, gold])
    assert len(result) == 2
    assert raw in result
    assert gold in result


def test_merge_spans_different_asset_classes_do_not_merge() -> None:
    """asset_class가 다르면 다른 축이므로 병합하지 않는다."""
    crypto = _span(asset_class=AssetClass.CRYPTO, start_at=_dt(1), end_at=_dt(5))
    kr_equity = _span(asset_class=AssetClass.KR_EQUITY, start_at=_dt(1), end_at=_dt(5))
    result = merge_spans([crypto, kr_equity])
    assert len(result) == 2
    assert crypto in result
    assert kr_equity in result


def test_merge_spans_different_instruments_do_not_merge() -> None:
    """instrument_id가 다르면 다른 축이므로 병합하지 않는다."""
    inst_a = _span(instrument_id="01ARZ3NDEKTSV4RRFFQ69G5FAV", start_at=_dt(1), end_at=_dt(5))
    inst_b = _span(instrument_id="01ARZ3NDEKTSV4RRFFQ69G5FB0", start_at=_dt(1), end_at=_dt(5))
    result = merge_spans([inst_a, inst_b])
    assert len(result) == 2
    assert inst_a in result
    assert inst_b in result


def test_coverage_for_merges_within_matching_axis_only() -> None:
    """coverage_for는 instrument×tf로 필터링한 뒤 같은 축 안에서만 병합한다 —
    다른 instrument의 span은 결과에 포함되지 않는다."""
    instrument = _instrument()
    other = _instrument(instrument_id="01ARZ3NDEKTSV4RRFFQ69G5FB0")
    my_span_a = _span(start_at=_dt(1), end_at=_dt(3))
    my_span_b = _span(start_at=_dt(3), end_at=_dt(5))  # 인접 -> 병합
    other_span = _span(instrument_id=other.instrument_id, start_at=_dt(1), end_at=_dt(10))
    result = coverage_for([my_span_a, my_span_b, other_span], instrument, Timeframe.D1)
    assert result == [_span(start_at=_dt(1), end_at=_dt(5))]
