"""L4_analytics_authoring_backtest_marketplace_v1.0.md §9.4 DSL-8 — M2-2b
(task-3977, ADR-2026-09-09-B) `request(symbol, timeframe, expr)` MTF(multi
timeframe) 런타임. 순수·I/O 없음.

lookahead=off 고정: 상위 타임프레임 봉은 "확정(닫힌) 버킷"만 참조한다. 기준
봉 인덱스 ``i`` 시점에 아직 닫히지 않은(진행 중인) 상위 버킷은 절대 값을
제공하지 않는다 — 그 버킷의 마지막 원소가 미래(``>= i``)이기 때문이다. 이
불변식(`confirmed_source_index(i, ratio) <= i`, 실제로는 항상 `< i` 또는
warm-up 구간의 `== 0`)은 `tests/unit/core/script/test_request_future_reference.py`
의 hypothesis property 테스트가 무작위 `(i, ratio)`에 대해 독립적으로
검증한다.

타임프레임 표기는 `<정수><단위>` (`m`/`h`/`D`/`W`) 하나만 지원한다(M2-2a가
문자열 리터럴로 고정한 형식을 여기서 처음 해석). 상위 타임프레임 리샘플만
지원하고(요청 타임프레임 < 기준 타임프레임은 합성 불가 — 순수 인터프리터는
기준 봉보다 더 잘게 쪼갤 데이터를 만들어낼 I/O가 없다), 배수가 아니면
거부한다(fail-closed, 조용한 반올림 없음).
"""
from __future__ import annotations

import re
from typing import Final

from src.core.script.runtime.series import ScriptRuntimeError, Series, Value, broadcast

_TIMEFRAME_RE: Final = re.compile(r"^(\d+)([mhDW])$")
_UNIT_MINUTES: Final[dict[str, int]] = {"m": 1, "h": 60, "D": 24 * 60, "W": 7 * 24 * 60}


def timeframe_minutes(timeframe: str) -> int:
    """`"5m"`/`"1h"`/`"1D"`/`"1W"` 형식을 분 단위 정수로 해석한다. 그 외 형식은 거부."""
    match = _TIMEFRAME_RE.match(timeframe)
    if match is None:
        raise ScriptRuntimeError(
            f"알 수 없는 타임프레임 형식입니다(정수+m/h/D/W만 지원): {timeframe!r}"
        )
    count = int(match.group(1))
    if count <= 0:
        raise ScriptRuntimeError(f"타임프레임 배수는 1 이상이어야 합니다: {timeframe!r}")
    return count * _UNIT_MINUTES[match.group(2)]


def resolve_ratio(base_timeframe: str, request_timeframe: str) -> int:
    """요청 타임프레임이 기준 타임프레임의 몇 배인지(상위 버킷 하나 = 기준봉
    `ratio`개) 계산한다. 배수가 아니면 거부(fail-closed) — 요청 타임프레임이
    기준보다 짧은 경우는 항상 이 조건에 걸린다(정수 `k>=1`에 대해
    `request = base * k`가 성립하려면 `request >= base`가 필요하므로, "기준보다
    짧은 타임프레임 합성 불가"는 별도 분기 없이 이 나눗셈 검사 하나로 막힌다)."""
    base_minutes = timeframe_minutes(base_timeframe)
    request_minutes = timeframe_minutes(request_timeframe)
    if request_minutes % base_minutes != 0:
        raise ScriptRuntimeError(
            f"요청 타임프레임({request_timeframe})이 기준 타임프레임"
            f"({base_timeframe})의 정수배가 아닙니다 — 상위 타임프레임 리샘플만 지원합니다"
        )
    return request_minutes // base_minutes


def confirmed_source_index(bar_index: int, ratio: int) -> int:
    """기준봉 `bar_index` 시점에 참조 가능한 "가장 최근에 확정된" 상위 버킷의
    마지막 기준봉 인덱스. `ratio`개 기준봉마다 상위 버킷 하나가 닫힌다.

    버킷 `b`(기준봉 `[b*ratio, (b+1)*ratio)`)는 `bar_index >= (b+1)*ratio`일
    때만 이미 닫혀 있다 — 즉 `bar_index`가 속한 버킷(`bar_index // ratio`,
    아직 진행 중)은 절대 후보가 아니다. 반환값은 항상 `<= bar_index`(미래
    참조 불가 불변식) — 첫 상위 버킷이 아직 닫히지 않은 warm-up 구간은 기준봉
    0의 값으로 대체한다(그 값도 `bar_index`의 과거/현재이지 미래가 아니다)."""
    if bar_index < 0:
        raise ScriptRuntimeError(f"기준봉 인덱스는 0 이상이어야 합니다: {bar_index}")
    if ratio < 1:
        raise ScriptRuntimeError(f"ratio는 1 이상이어야 합니다: {ratio}")
    last_closed_bucket = bar_index // ratio - 1
    if last_closed_bucket < 0:
        return 0
    return (last_closed_bucket + 1) * ratio - 1


def resample_confirmed(source: Series, *, bar_count: int, ratio: int) -> Series:
    """`source`(기준 타임프레임 시리즈)를 `ratio`배 상위 타임프레임의 확정봉만
    참조하도록 리샘플한다. 결과도 길이 `bar_count`의 기준봉 정렬 시리즈다
    (기준 봉마다 하나씩 채워 넣는 sample-and-hold — 스칼라 승격과 동일한
    "봉 차원" 규칙을 따른다)."""
    if len(source) != bar_count:
        raise ScriptRuntimeError(
            f"request() 내부 시리즈 길이 {len(source)} != 봉 수 {bar_count}"
        )
    values = source.values
    return Series(tuple(values[confirmed_source_index(i, ratio)] for i in range(bar_count)))


def evaluate_request(
    value: Value,
    *,
    bar_count: int,
    symbol: str | None,
    base_timeframe: str | None,
    request_symbol: str,
    request_timeframe: str,
) -> Series:
    """`interpreter._Machine._request`의 `Request` 명령 평가 본체. 내부 expr 값
    (이미 post-order로 스택에서 꺼내진 `value`)을 기준 타임프레임 시리즈로 펴고
    (`broadcast`), 확정봉 전용 리샘플을 적용한다. symbol이 실행 시점 기준
    symbol과 다르면 거부한다(순수 인터프리터는 타 심볼 데이터를 조회할 I/O가
    없다 — 동일 심볼 MTF만 지원)."""
    if base_timeframe is None:
        raise ScriptRuntimeError(
            "request(...)를 실행하려면 execute(base_timeframe=...)가 필요합니다"
        )
    if request_symbol != symbol:
        raise ScriptRuntimeError(
            f"request(...)의 symbol({request_symbol!r})이 execute(symbol={symbol!r})"
            "과 다릅니다 — 순수 인터프리터는 타 심볼 데이터를 조회할 I/O가 없어"
            " 동일 심볼 MTF만 지원합니다"
        )
    ratio = resolve_ratio(base_timeframe, request_timeframe)
    return resample_confirmed(broadcast(value, bar_count), bar_count=bar_count, ratio=ratio)
