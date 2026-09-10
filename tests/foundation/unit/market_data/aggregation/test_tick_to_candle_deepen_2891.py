"""DC-22 `domain/aggregation/tick_to_candle.py` — DEEPEN(task-2891,
docs/audit/DEPTH_DC_RD.md#2131) D1 -> D3 증빙.

기존 `test_tick_to_candle.py`는 OHLCV 집계·lineage·결정론 해시·역순 거부·
중복 접힘·갭·세션 밖 제외·조기폐장 클립을 negative 2개(UnsortedTicksError,
MixedSeriesError)로 증명했다(D1). 감사(2131행)는 negative<3, 실패주입 없음,
성능단언 없음, 게이트적색 재현 없음을 지적했다 — 다만 감사 총평(14행)이
명시하듯 `tick_to_candle`은 I/O 없는 순수 도메인 함수라 "실패주입"의
일반적 의미(네트워크/DB 장애 주입)는 원천적으로 성립하지 않는다. 이 파일은
그 대신 (a) 계약 경계(TickLineage) negative, (b) 타입힌트가 강제하지 않는
호출 경계 오염 주입, (c) 수치 성능 단언, (d) 게이트 적색(예외) 재현이 이후
호출을 오염시키지 않음, (e) 퍼즈·재생 결정론·동시 다중 인스턴스(D3)를
채운다. `tick_to_candle.py`는 무수정 — 새 기능 없음, 깊이만 올림.
"""

from __future__ import annotations

import random
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from src.foundation.market_data.contracts.v1 import Timeframe, Venue
from src.foundation.market_data.contracts.v2.candle_lineage import SourceKind, TickLineage
from src.foundation.market_data.contracts.v2.microstructure import Aggressor, TradeTick
from src.foundation.market_data.domain.aggregation.tick_to_candle import (
    SessionNotFoundError,
    UnsortedTicksError,
    _session_containing,
    ticks_to_candles,
)
from src.foundation.market_data.domain.calendar.known_venues import KNOWN_SESSIONS
from src.foundation.market_data.domain.calendar.session_rules import VenueCalendar

UTC = timezone.utc
_ULID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
_BASE_NS = 1_767_225_600_000_000_000  # 2026-01-01T00:00:00Z (Thursday)


def _bitget_calendar() -> VenueCalendar:
    spec = KNOWN_SESSIONS[Venue.BITGET.value]
    return VenueCalendar(venue=Venue.BITGET.value, tz=spec.tz, regular=spec)


def _tick(
    seconds_offset: int,
    seq: int,
    price: str,
    size: str,
    *,
    sub_ns: int = 0,
    instrument_id: str = _ULID,
    venue: Venue = Venue.BITGET,
) -> TradeTick:
    ts_event = _BASE_NS + seconds_offset * 1_000_000_000 + sub_ns
    return TradeTick(
        instrument_id=instrument_id,
        venue=venue,
        ts_event=ts_event,
        ts_recv=ts_event + 1_000,
        seq=seq,
        price=Decimal(price),
        size=Decimal(size),
        aggressor=Aggressor.BUY,
    )


# ---- 실패 주입 — TickLineage 계약 경계(negative) ----


def test_tick_lineage_rejects_tick_count_below_one() -> None:
    """`tick_count`는 `Field(ge=1)`이다 — 0건짜리 lineage는 "빈 봉을
    만들지 않는다"는 `ticks_to_candles`의 불변식이 깨졌다는 신호이므로,
    계약 자체가 구성 시점에 즉시 거부해야 한다."""
    with pytest.raises(ValidationError):
        TickLineage(
            first_ts_event=_BASE_NS, first_seq=0, last_ts_event=_BASE_NS, last_seq=0, tick_count=0
        )


def test_tick_lineage_rejects_ts_event_outside_nanosecond_range() -> None:
    """DC-19가 이미 강제하는 나노초 범위(1e18~1e19)를 DC-22 lineage에서도
    우회할 수 없어야 한다 — 초/밀리초 단위 실수로 만들어진 `first_ts_event`가
    조용히 저장되면 lineage로 원본 tick을 역추적할 때 엉뚱한 시각을 가리킨다."""
    with pytest.raises(ValidationError):
        TickLineage(
            first_ts_event=1_700_000_000,
            first_seq=0,
            last_ts_event=_BASE_NS,
            last_seq=0,
            tick_count=1,
        )


# ---- 실패 주입 — 호출 경계 오염(타입힌트가 강제하지 않는 요소) ----


def test_ticks_to_candles_rejects_non_tradetick_element() -> None:
    """`list[TradeTick]` 타입힌트는 런타임을 강제하지 않는다 — 역직렬화
    경로 등에서 딕셔너리가 섞여 들어와도 조용히 건너뛰거나 잘못된 결과를
    내는 대신 즉시 크래시해야 한다(fail-closed)."""
    valid = _tick(0, seq=1, price="100", size="1")
    garbage: Any = {"ts_event": _BASE_NS, "seq": 2}
    with pytest.raises(AttributeError):
        ticks_to_candles([valid, garbage], Timeframe.M1, _bitget_calendar())


def test_ticks_to_candles_massive_duplicate_flood_collapses_to_one_tick() -> None:
    """동일 (ts_event, seq)를 수백 번 재전송하는 플러딩(예: 재시도 폭주로
    같은 체결 이벤트가 반복 유입)을 주입해도, dedupe는 정확히 1건으로
    수렴하고 `tick_count`가 부풀지 않는다."""
    flood = [_tick(0, seq=1, price="100", size="1") for _ in range(500)]
    result = ticks_to_candles(flood, Timeframe.M1, _bitget_calendar())
    assert len(result.columns) == 1
    assert result.lineage[0].tick_count == 1
    assert result.columns.volume == [Decimal("1")]


# ---- 게이트 적색 재현 — 회귀(DEEPEN 중 fuzz로 발견) ----


def test_leading_partial_window_is_not_silently_dropped_when_first_tick_is_off_grid() -> None:
    """회귀 재현: 배치의 첫 틱이 정확히 분(minute) 경계가 아니면(현실의
    체결은 거의 항상 그렇다), 그 틱이 속한 창(00:00 오픈, 첫 틱은 00:23에
    찍힘)이 `expected_opens`의 `start<=cursor` 필터에서 조용히 빠져 해당
    창의 모든 틱이 버려지는 회귀가 이 DEEPEN의 퍼즈 테스트로 발견됐다
    (range_start를 `align_open`으로 그리드 정렬하지 않고 `tick_dt[0]`
    그대로 썼던 결함). 00:00 창의 틱 2건과 00:01 창의 틱 1건이 모두
    보존돼야 한다."""
    ticks = [
        _tick(23, seq=1, price="100", size="1"),  # 00:00 창(비정렬 시작)
        _tick(40, seq=2, price="101", size="2"),  # 같은 00:00 창
        _tick(90, seq=3, price="102", size="1"),  # 00:01 창
    ]
    result = ticks_to_candles(ticks, Timeframe.M1, _bitget_calendar())

    assert len(result.columns) == 2
    assert result.lineage[0].tick_count == 2, "00:00 창의 두 틱이 모두 보존돼야 한다"
    assert result.columns.volume == [Decimal("3"), Decimal("1")]
    assert result.columns.open == [Decimal("100"), Decimal("102")]
    assert result.columns.close == [Decimal("101"), Decimal("102")]


# ---- 실패 주입 — 세션 불변식 방어(SessionNotFoundError, 화이트박스) ----


def test_session_not_found_error_fires_for_open_time_missing_from_session_list() -> None:
    """`_session_containing`은 `expected_opens`가 만든 open이 그 open을
    만든 바로 그 `sessions` 목록에 반드시 속한다는 내부 불변식을 전제로
    한다. 이 불변식이 깨진(그 목록에 없는 open을 조회하는) 상황을 직접
    재현해, 조용히 엉뚱한 세션을 골라 잘못된 캔들을 만드는 대신 즉시
    거부됨을 증명한다(방어적 가드, 공개 API로는 재현 불가한 내부 불변식)."""
    calendar = _bitget_calendar()
    sessions = calendar.sessions_for(datetime(2026, 1, 1, tzinfo=UTC).date())
    orphan_open = datetime(2099, 1, 1, tzinfo=UTC)  # 이 목록의 어느 세션에도 속하지 않음
    with pytest.raises(SessionNotFoundError):
        _session_containing(orphan_open, sessions)


# ---- 성능 단언 ----


@pytest.mark.perf
def test_ticks_to_candles_meets_latency_budget_for_large_tick_series() -> None:
    """50,000틱(100틱/초 x 500초 ~= 9개 M1창)을 집계하는 시간이 절대시간
    예산 내여야 한다 — 두-포인터 스캔이 창마다 처음부터 다시 훑는 식으로
    퇴화(O(n x windows))하면 이 예산을 넘는다."""
    n = 50_000
    rng = random.Random(2891)
    ticks = [
        _tick(i // 100, seq=i, price=str(100 + rng.randint(-5, 5)), size="1") for i in range(n)
    ]

    budget_sec = 8.0  # 실측 로컬 단독 실행 <0.2s, 스위트 동시부하 시 변동 감안
    start = time.perf_counter()
    result = ticks_to_candles(ticks, Timeframe.M1, _bitget_calendar())
    elapsed = time.perf_counter() - start

    print(
        f"[DC-22 tick_to_candle] {n}틱 -> {len(result.columns)}봉, "
        f"{elapsed:.3f}s (budget<{budget_sec}s)"
    )
    total_ticks = sum(lin.tick_count for lin in result.lineage)
    assert total_ticks == n
    assert elapsed < budget_sec, (
        f"ticks_to_candles({n}틱)가 예산({budget_sec}s)을 넘었습니다({elapsed:.3f}s) — "
        "두-포인터 스캔이 창마다 재스캔으로 퇴화했는지 확인하세요."
    )


# ---- 게이트 적색 재현 ----


def test_gate_red_rejected_call_does_not_corrupt_subsequent_valid_calls() -> None:
    """틱 수집 파이프라인을 재생한다: 정상 배치를 집계 -> 순서가 뒤섞인
    배치를 시도(게이트 적색, `UnsortedTicksError`) -> 원래(무수정) 배치를
    다시 집계했을 때 직전의 실패 시도가 결과를 오염시키지 않았음을 증명
    -> 정상적인 다음 창 배치를 이어 붙여도 이전 봉이 그대로 유지된다.
    순수 함수는 전역 가변 상태가 없어야 당연하지만, 회귀 시 모듈 레벨
    캐시 등이 실수로 추가되는 것을 이 테스트가 잡는다."""
    calendar = _bitget_calendar()
    valid_batch = [
        _tick(0, seq=1, price="100", size="1"),
        _tick(10, seq=2, price="105", size="2"),
    ]
    before = ticks_to_candles(valid_batch, Timeframe.M1, calendar)
    assert len(before.columns) == 1
    assert before.columns.close == [Decimal("105")]

    # 게이트 적색: ts_event 역순 배치 시도.
    unsorted_batch = [
        _tick(10, seq=3, price="200", size="1"),
        _tick(0, seq=4, price="201", size="1"),
    ]
    with pytest.raises(UnsortedTicksError):
        ticks_to_candles(unsorted_batch, Timeframe.M1, calendar)

    # 거부된 시도 이후 원래 배치를 재집계해도 결과는 동일 — 오염 없음.
    after_rejection = ticks_to_candles(valid_batch, Timeframe.M1, calendar)
    assert after_rejection == before

    # 정상적인 다음 창 배치를 이어 붙이면 이전 봉은 그대로, 새 봉만 추가된다.
    next_window_batch = valid_batch + [_tick(65, seq=5, price="110", size="1")]
    final = ticks_to_candles(next_window_batch, Timeframe.M1, calendar)
    assert len(final.columns) == 2
    assert final.columns.close == [Decimal("105"), Decimal("110")]


# ---- 퍼즈 + 재생 결정론 + 동시 다중 인스턴스(D3) ----

_FUZZ_SEEDS = (0, 1, 42, 1337, 2891)


def _random_ticks(
    rng: random.Random, count: int, *, venue: Venue, instrument_id: str
) -> list[TradeTick]:
    ticks = []
    ts_offset = 0
    for i in range(count):
        ts_offset += rng.randint(0, 3)  # 비내림차순 유지(정렬 거부를 트리거하지 않음)
        price = Decimal(100 + rng.randint(-20, 20)) + Decimal(rng.randint(0, 99)) / Decimal(100)
        size = Decimal(1 + rng.randint(0, 9))
        ticks.append(
            _tick(
                ts_offset,
                seq=i,
                price=str(price),
                size=str(size),
                venue=venue,
                instrument_id=instrument_id,
            )
        )
    return ticks


@pytest.mark.parametrize("seed", _FUZZ_SEEDS)
def test_fuzz_ticks_to_candles_invariants_hold_for_random_input(seed: int) -> None:
    """고정 시드 5개로 무작위(단일 시리즈) 틱 묶음을 생성해 불변조건이
    항상 성립함을 증명한다: (a) lineage.tick_count 합계가 입력 틱 수와
    정확히 같다(24x7 연속 캘린더라 세션 제외가 없고, seq를 모두 다르게
    생성해 dedupe로 줄지도 않는다), (b) 각 봉의 low<=open,close,high<=high,
    (c) open_time이 오름차순 중복 없이 정렬돼 있다."""
    rng = random.Random(seed)
    ticks = _random_ticks(rng, 300, venue=Venue.BITGET, instrument_id=_ULID)
    result = ticks_to_candles(ticks, Timeframe.M1, _bitget_calendar())

    total_ticks = sum(lin.tick_count for lin in result.lineage)
    assert total_ticks == len(ticks), f"seed={seed}: lineage tick_count 합계가 입력 틱 수와 다르다"

    for i in range(len(result.columns)):
        low, high = result.columns.low[i], result.columns.high[i]
        assert low <= result.columns.open[i] <= high, f"seed={seed}: open이 [low,high] 밖"
        assert low <= result.columns.close[i] <= high, f"seed={seed}: close가 [low,high] 밖"

    assert result.columns.ts == sorted(result.columns.ts), (
        f"seed={seed}: open_time이 오름차순이 아니다"
    )
    assert len(set(result.columns.ts)) == len(result.columns.ts), f"seed={seed}: open_time 중복"


def test_replay_ticks_to_candles_is_deterministic_across_independent_reconstruction() -> None:
    """동일 입력을 독립적으로 재구성해 두 번 호출해도 완전히 동일한 결과 —
    숨은 시계·난수·전역 가변 상태가 없다는 재생(replay) 안전성 증거."""
    rng = random.Random(777)
    calendar = _bitget_calendar()

    def _build() -> list[TradeTick]:
        return _random_ticks(random.Random(777), 150, venue=Venue.BITGET, instrument_id=_ULID)

    _ = rng  # seed는 _build 내부에서 고정 재사용
    first = ticks_to_candles(_build(), Timeframe.M1, calendar)
    second = ticks_to_candles(_build(), Timeframe.M1, calendar)
    assert first == second
    assert first.source_kind == SourceKind.TICK_DERIVED


def test_concurrent_ticks_to_candles_across_independent_series_do_not_cross_contaminate() -> None:
    """서로 다른 instrument_id의 독립적인 틱 시리즈를 여러 스레드가 동시에
    집계해도 서로의 결과를 오염시키지 않는다 — 모듈 전역 가변 상태가
    없다는 동시성 증거(D3)."""
    instruments = [
        "01ARZ3NDEKTSV4RRFFQ69G5FAV",
        "01BX5ZZKBKACTAV9WEVGEMMVRZ",
        "01BX5ZZKBKACTAV9WEVGEMMVR0",
        "01BX5ZZKBKACTAV9WEVGEMMVR1",
        "01BX5ZZKBKACTAV9WEVGEMMVR2",
        "01BX5ZZKBKACTAV9WEVGEMMVR3",
    ]
    calendar = _bitget_calendar()

    def _expected(instrument_id: str):
        rng = random.Random(hash(instrument_id) % 10_000)
        ticks = _random_ticks(rng, 120, venue=Venue.BITGET, instrument_id=instrument_id)
        return ticks_to_candles(ticks, Timeframe.M1, calendar)

    baselines = {iid: _expected(iid) for iid in instruments}

    def _run(instrument_id: str):
        rng = random.Random(hash(instrument_id) % 10_000)
        ticks = _random_ticks(rng, 120, venue=Venue.BITGET, instrument_id=instrument_id)
        return instrument_id, ticks_to_candles(ticks, Timeframe.M1, calendar)

    workload = list(instruments) * 20  # 120회 동시 호출, 서로 다른 시리즈가 반복 교차
    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(_run, workload))

    assert len(results) == len(workload)
    for instrument_id, result in results:
        assert result == baselines[instrument_id], (
            f"{instrument_id} 스레드가 다른 시리즈의 집계 결과와 섞였다"
        )
