"""RD-9 후속(task-4075) -- `domain/as_of_binding.py` + `adapters/dsl_query.py`
D2 하한 증빙(ADR-2026-09-09-C Decision 1).

task-2710(RD-9 핵심 구현)은 3회 연속 턴 한도로 범위가 축소되어
`tests/unit/research_data/test_as_of_binding.py` + `test_dsl_query.py`에
기본 negative 커버리지만 남겼다(두 파일 모듈 docstring 참조). 이 파일이
D2 하한의 나머지를 채운다: negative >=3(신규, 기존 파일과 중복 없음),
실패주입 1, 수치 성능 단언 1(처리량), 게이트 적색 재현 1.

RD는 ADR-2026-09-09-C의 D3 필수 축(R, L4, LA/LB/LC, FA, CM, EO, DC) 목록에
없으므로 D2가 이 리프의 바닥이다 -- adversarial/replay_verify 요구 없음.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from src.core.script.runtime.interpreter_types import CallSite
from src.foundation.market_data.domain.candle_columns import CandleColumns
from src.foundation.research_data.adapters.dsl_query import (
    ResearchDslQueryError,
    research_builtins,
)
from src.foundation.research_data.contracts.v1 import ResearchItem
from src.foundation.research_data.domain.as_of_binding import AsOfBindingError, bind_as_of
from tests.conftest import PerfBudget

_INSTRUMENT = "KR:A005930"
_BASE = datetime(2026, 6, 1, tzinfo=timezone.utc)


def _item(
    *, known_at: datetime, kind: str = "filing", instrument: str = _INSTRUMENT
) -> ResearchItem:
    return ResearchItem(
        item_id=uuid4(),
        source_id="test-source",
        kind=kind,
        published_at=known_at,
        known_at=known_at,
        instruments=(instrument,),
        title="test",
        body_ref="/test/ref",
        url="https://example.com/test",
        language="ko",
        hash=str(uuid4()),
        revision_of=None,
    )


def _columns(ts: list[datetime]) -> CandleColumns:
    from decimal import Decimal

    n = len(ts)
    one = [Decimal("1")] * n
    return CandleColumns(
        ts=ts, open=one, high=one, low=one, close=one, volume=one, quote_volume=[None] * n
    )


def _linear_ts(n: int) -> list[datetime]:
    return [_BASE + timedelta(days=i) for i in range(n)]


# ---- negative (>=3, 기존 test_as_of_binding.py/test_dsl_query.py와 중복 없음) ----


def test_naive_column_timestamp_propagates_as_tzaware_rejection_through_builtin() -> None:
    """`CandleColumns.ts`에 naive datetime이 섞여 들어오면(상류 버그),
    dsl_query가 이를 조용히 UTC로 보정하지 않고 `bind_as_of`의 tz-aware
    거부가 빌트인 호출 경계까지 그대로 전파돼야 한다 -- 침묵 실패 금지."""
    ts = _linear_ts(3)
    ts[1] = ts[1].replace(tzinfo=None)  # 중간 bar만 naive로 오염
    columns = _columns(ts)
    table = research_builtins([], columns, instrument=_INSTRUMENT)
    builtin = table[("research", "filing_count")]
    site = CallSite("research", "filing_count", "series<float>", 3)

    with pytest.raises(ValueError, match="tz-aware"):
        builtin((), site)


def test_far_future_known_at_item_never_leaks_into_any_early_bar() -> None:
    """`known_at`이 수백 년 뒤인 항목이 섞여도(정수 오버플로/비교 실수
    없이) 그 어떤 과거 bar에도 새어들면 안 된다(RD-A1 극단값 테스트)."""
    far_future_item = _item(known_at=datetime(3000, 1, 1, tzinfo=timezone.utc))
    columns = _columns(_linear_ts(5))
    table = research_builtins([far_future_item], columns, instrument=_INSTRUMENT)
    builtin = table[("research", "filing_count")]
    site = CallSite("research", "filing_count", "series<float>", 5)

    result = builtin((), site)

    assert [result.at(i) for i in range(5)] == [0.0, 0.0, 0.0, 0.0, 0.0]


def test_kind_isolation_holds_across_full_bar_range_with_mixed_kinds() -> None:
    """단일 시점(test_dsl_query.py `test_kind_isolation`)이 아니라 전체
    타임라인에 걸쳐 4종(kind)이 서로 다른 known_at으로 섞여도 각 카운터가
    항상 자신의 kind만 세는지 -- 누적 카운트가 다른 kind의 항목 수에
    영향받으면 이 테스트가 잡는다."""
    ts = _linear_ts(6)
    items = [
        _item(known_at=ts[0], kind="filing"),
        _item(known_at=ts[1], kind="news"),
        _item(known_at=ts[1], kind="news"),
        _item(known_at=ts[3], kind="macro"),
        _item(known_at=ts[5], kind="alt"),
    ]
    columns = _columns(ts)
    table = research_builtins(items, columns, instrument=_INSTRUMENT)
    site = CallSite("research", "filing_count", "series<float>", 6)

    filing_result = table[("research", "filing_count")]((), site)
    news_result = table[("research", "news_count")]((), site)
    macro_result = table[("research", "macro_count")]((), site)
    alt_result = table[("research", "alt_count")]((), site)

    assert [filing_result.at(i) for i in range(6)] == [1.0] * 6
    assert [news_result.at(i) for i in range(6)] == [0.0, 2.0, 2.0, 2.0, 2.0, 2.0]
    assert [macro_result.at(i) for i in range(6)] == [0.0, 0.0, 0.0, 1.0, 1.0, 1.0]
    assert [alt_result.at(i) for i in range(6)] == [0.0, 0.0, 0.0, 0.0, 0.0, 1.0]


def test_cross_instrument_and_future_reference_both_rejected_or_excluded_simultaneously() -> None:
    """교차 종목 항목(리키지 후보)과 미래 known_at 항목(RD-A1 후보)이
    같은 후보 목록에 함께 있어도 각각 독립적으로 걸러져야 한다."""
    ts = _linear_ts(3)
    other_instrument = _item(known_at=ts[0], instrument="KR:A000001")
    future = _item(known_at=ts[0] + timedelta(days=100))
    columns = _columns(ts)
    table = research_builtins([other_instrument, future], columns, instrument=_INSTRUMENT)
    builtin = table[("research", "filing_count")]
    site = CallSite("research", "filing_count", "series<float>", 3)

    result = builtin((), site)

    assert [result.at(i) for i in range(3)] == [0.0, 0.0, 0.0]


# ---- 실패 주입 ---------------------------------------------------------------


def test_failed_call_does_not_corrupt_state_for_a_subsequent_correct_call() -> None:
    """실패 주입: 잘못된 인자로 한 번 실패한 뒤(예외 발생), 같은 빌트인을
    올바른 인자로 다시 호출하면 정상 동작해야 한다 -- `_KnownCountBuiltin`은
    frozen dataclass이므로 실패한 호출이 내부 상태를 변형해 이후 호출에
    영향을 주면 안 된다(부작용 없는 순수 호출 경계 증명)."""
    items = [_item(known_at=_BASE)]
    columns = _columns(_linear_ts(2))
    table = research_builtins(items, columns, instrument=_INSTRUMENT)
    builtin = table[("research", "filing_count")]
    bad_site = CallSite("research", "filing_count", "series<float>", 2)

    with pytest.raises(ResearchDslQueryError, match="takes no arguments"):
        builtin((1.0,), bad_site)

    # 실패 이후에도 동일 빌트인 인스턴스가 정상적으로 재호출 가능해야 한다.
    result = builtin((), bad_site)
    assert [result.at(i) for i in range(2)] == [1.0, 1.0]


def test_bind_as_of_rejection_repeats_deterministically_under_repeated_retries() -> None:
    """실패 주입: 호출자가 같은 미래 값으로 재시도해도 매번 동일하게
    거부돼야 한다 -- 첫 실패 이후 내부에 클램프된 값이 남아 두 번째부터
    통과하는 회귀를 잡는다(bind_as_of 자체는 순수 함수라 상태가 없어야
    한다는 계약을 반복 호출로 증명)."""
    bar_ts = _BASE
    future = bar_ts + timedelta(days=1)
    for _attempt in range(5):
        with pytest.raises(AsOfBindingError):
            bind_as_of(bar_ts, requested_as_of=future)
    # 거부 이후에도 정상 경로(과거 시점 요청)는 계속 동작해야 한다.
    assert bind_as_of(bar_ts, requested_as_of=bar_ts - timedelta(hours=1)) == bar_ts - timedelta(
        hours=1
    )


# ---- 성능 단언 (처리량) -------------------------------------------------------


@pytest.mark.perf
def test_research_builtin_meets_throughput_budget_over_5000_bars(perf_budget: PerfBudget) -> None:
    """5,000봉(바) 백테스트 구간에 대해 `research.filing_count()`를 평가하는
    비용이 절대시간 예산 내여야 한다 -- ADR-2026-09-09-C의 "5k봉 조회 p95
    200ms" 예산과 동일 규모(5k)를 기준으로, 여기서는 `search()` 선형 스캔이
    bar마다 반복 호출되므로(§dsl_query.py `_KnownCountBuiltin.__call__`)
    더 넉넉한 절대 예산을 건다(순수 파이썬 반복 + 선형 스캔 100개 항목
    x 5,000회 = 500,000 비교 상당). task-6774 -- 공용 `perf_budget`
    픽스처(`time.process_time()` 기준 best-of-5)로 통일해 부하 중 이 단일
    wall-clock 측정이 흔들리던 문제를 없앤다."""
    n_bars = 5_000
    n_items = 100
    budget_ms = 3_000.0  # 실측 로컬 <500ms, CI 편차 감안
    ts = _linear_ts(n_bars)
    items = [_item(known_at=ts[i * (n_bars // n_items)]) for i in range(n_items)]
    columns = _columns(ts)
    table = research_builtins(items, columns, instrument=_INSTRUMENT)
    builtin = table[("research", "filing_count")]
    site = CallSite("research", "filing_count", "series<float>", n_bars)

    perf_budget.assert_within(
        lambda: builtin((), site),
        budget_ms=budget_ms,
        label=f"filing_count() over {n_bars} bars x {n_items} items",
    )
    result = builtin((), site)
    assert result.at(n_bars - 1) == float(n_items)


# ---- 게이트 적색 재현 ---------------------------------------------------------


def test_gate_red_correction_chain_replayed_stage_by_stage_never_leaks_future_count() -> None:
    """단일 종목에 대해 10건의 filing이 하루 간격으로 known_at을 갖도록
    쌓아두고, bar-by-bar로 재생한다. 각 bar는 정확히 "그 bar까지 known_at이
    도달한 항목 수"만 봐야 한다 -- 이전 bar 결과가 캐시처럼 새거나(카운트가
    갱신되지 않음), 다음 bar 값이 앞당겨 보이면(RD-A1 리키지) 이 테스트가
    적색이 된다. `bind_as_of`가 각 bar의 `columns.ts[i]`에 독립적으로
    바인딩됨을 증명한다(§dsl_query.py 33-37행)."""
    n = 10
    ts = _linear_ts(n)
    items = [_item(known_at=ts[i]) for i in range(n)]
    columns = _columns(ts)
    table = research_builtins(items, columns, instrument=_INSTRUMENT)
    builtin = table[("research", "filing_count")]
    site = CallSite("research", "filing_count", "series<float>", n)

    result = builtin((), site)

    for stage in range(n):
        expected = float(stage + 1)  # known_at <= ts[stage] 인 항목 수
        assert result.at(stage) == expected, (
            f"stage {stage}: expected {expected}, got {result.at(stage)}"
        )

    # 재생 후에도 원본 items가 변형되지 않았다(순수성 -- revision_of 등 필드 불변).
    assert len(items) == n
    assert items[0].known_at == ts[0]
