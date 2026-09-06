"""IND-10 — TA-Lib 161종 메타데이터 → `IndicatorSpec` 자동 생성.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §9.9 IND-10
(선행 IND-9·IND-1, ADR-2026-09-06-F D1).

`talib.get_functions()`(161종) × `abstract.Function(name).info`를 순회해 스펙을
**생성**한다(수기 작성 금지) — group→카테고리(`TALIB_GROUPS`), parameters(기본값)→
정수 파라미터 범위 규칙, `.lookback`→실측 lookback, output_names→출력 계약,
output_flags/function_flags→`PlotSpec` 기본값.

부동소수 파라미터(nbdevup·acceleration·penetration 등, 161종 중 14종)는 이번
생성 단계에서 `ParamSpec`으로 노출하지 않는다 — `IndicatorRegistry`/`engine/
incremental.py`·`engine/vectorized.py`(IND-1, 이 leaf 밖)가 파라미터를
`dict[str, int]` 단일 타입으로 공유하므로, float를 1급으로 노출하려면 그 계약을
전부 `int | float`로 넓혀야 한다 — 이 leaf의 파일 범위(`talib_adapter.py`·
`registry.py`·`specs_talib.py`) 밖까지 번지는 변경이라 하지 않는다. 대신
TA-Lib 자신의 기본값을 그대로 쓴다(`talib.abstract.Function(name, **only_int_params)`
호출 시 미지정 float 파라미터는 TA-Lib이 자체 기본값을 적용 — 명시 전달과 값·
lookback이 바이트 동일함을 확인함). "정수 주기 1~2000·편차 0.1~10" 규칙 중
편차 쪽은 `_deviation_range()`로 순수 함수로만 구현해 두고(결정 노트의 규칙
자체는 존재), 아직 `ParamSpec`에 배선하지 않는다 — IND-12(레지스트리 3층화)
이후 필요해지면 그때 배선한다.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence

import talib
from talib import abstract as talib_abstract

from src.core.indicators.spec import (
    DefaultPane,
    IndicatorSpec,
    ParamSpec,
    PlotKind,
    PlotSpec,
    ScaleHint,
)

__all__ = [
    "TALIB_GROUPS",
    "generate_talib_specs",
]

_PERIOD_MIN = 1
_PERIOD_MAX = 2000
_MATYPE_MIN = 0
_MATYPE_MAX = 8  # talib.MA_Type: SMA(0)..MAMA(8)
_DEVIATION_MIN = 0.1
_DEVIATION_MAX = 10.0

_MATYPE_PARAM_NAMES = frozenset(
    {
        "matype",
        "fastmatype",
        "slowmatype",
        "signalmatype",
        "slowk_matype",
        "slowd_matype",
        "fastd_matype",
    }
)

_CANDLESTICK_FLAG = "Output is a candlestick"
_SAME_SCALE_FLAG = "Output scale same as input"
_HISTOGRAM_FLAG = "Histogram"
_UPPER_LIMIT_FLAG = "Values represent an upper limit"
_LOWER_LIMIT_FLAG = "Values represent a lower limit"


def _deviation_range(default: float) -> tuple[float, float]:
    """결정 노트의 "편차 0.1~10" 규칙(순수 함수). 아직 `ParamSpec`에 배선하지
    않는다 — 모듈 docstring 참조. 기본값이 규칙 범위를 벗어나면(SAR류 acceleration
    등) 기본값을 포함하도록 대칭 확장한다(항상 min <= default <= max 보장)."""
    if _DEVIATION_MIN <= default <= _DEVIATION_MAX:
        return _DEVIATION_MIN, _DEVIATION_MAX
    if default <= 0:
        return 0.0, _DEVIATION_MAX
    return min(_DEVIATION_MIN, default), max(_DEVIATION_MAX, default)


def _flatten_inputs(input_names: Mapping[str, object]) -> tuple[str, ...]:
    """`abstract.Function.info["input_names"]`를 캔들 필드 이름 평탄 튜플로 만든다.

    값이 `list`면(예: `{'prices': ['high','low','close']}`) 펼치고, 문자열이면
    (예: `{'price0': 'high'}`) 그대로 붙인다 — 순서는 info의 삽입 순서를 보존해야
    talib 함수 호출 시 위치 인자 순서와 맞는다.
    """
    flat: list[str] = []
    for value in input_names.values():
        if isinstance(value, list):
            flat.extend(value)
        else:
            flat.append(str(value))
    return tuple(flat)


def _int_param_specs(parameters: Mapping[str, object]) -> tuple[ParamSpec, ...]:
    """정수 파라미터만 `ParamSpec`으로 노출한다(float는 모듈 docstring 참조).

    matype류는 0~8(talib.MA_Type 종수), 그 외 정수는 주기 규칙 1~2000.
    """
    specs: list[ParamSpec] = []
    for name, default in parameters.items():
        if isinstance(default, float):
            continue
        if not isinstance(default, int):
            continue
        if name in _MATYPE_PARAM_NAMES:
            specs.append(ParamSpec(name=name, min=_MATYPE_MIN, max=_MATYPE_MAX, default=default))
        else:
            specs.append(ParamSpec(name=name, min=_PERIOD_MIN, max=_PERIOD_MAX, default=default))
    return tuple(specs)


def _style_for_function(function_flags: Sequence[str] | None) -> tuple[ScaleHint, DefaultPane]:
    """group 대신 `function_flags`(더 세밀한 talib 신호)로 스케일·페인을 정한다.

    "캔들스틱"·"입력과 같은 스케일" 둘 다 가격 위 오버레이(`overlay`/`price`)로
    그린다 — 후자는 SMA·BBANDS류(Overlap Studies·Price Transform), 전자는
    Pattern Recognition 61종(마커로 그릴 스케일이 가격과 같다). 그 외(모멘텀·
    거래량·변동성·사이클·수학 계열)는 자체 스케일 별도 페인.
    """
    flags = function_flags or []
    if _CANDLESTICK_FLAG in flags or _SAME_SCALE_FLAG in flags:
        return "overlay", "price"
    return "own", "separate"


def _plot_kind(flags: Sequence[str]) -> PlotKind:
    """TA-Lib output_flags 하나를 PlotSpec.kind로 매핑(ADR-2026-09-06-F D1)."""
    if _HISTOGRAM_FLAG in flags:
        return "histogram"
    if _UPPER_LIMIT_FLAG in flags or _LOWER_LIMIT_FLAG in flags:
        return "band"
    return "line"  # Line·Dashed Line 둘 다 line


def _plots_from_talib(
    talib_name: str,
    output_names: tuple[str, ...],
    style: Mapping[str, Mapping[str, object]],
) -> tuple[PlotSpec, ...]:
    """`talib_name`의 실제 output_flags에서 kind/fill_between을 도출해 PlotSpec을 만든다.

    `output_names`는 talib 원본 출력 순서와 1:1 대응하는(이름만 바꿔도 되는) 이
    모듈의 출력 이름이어야 한다 — 순서가 어긋나면 kind/fill_between이 잘못 배정된다.
    캔들스틱 함수는 output_flags가 `Line`이어도 실제로는 가격 위 마커이므로
    `kind="marker"`로 강제한다. histogram 출력은 부호로 색을 나누는 것이 자연스러운
    기본값이라 `color_rule`이 style에 없으면 "sign"을 자동 채운다.
    """
    # talib/abstract.pyi는 개별 지표 함수만 선언하고 런타임 전용 `Function` 소개자
    # 클래스는 선언하지 않는다(talib 패키지 stub 한계) — 값은 실제로 존재한다.
    info = talib_abstract.Function(talib_name).info  # type: ignore[attr-defined]
    flags_by_output: list[list[str]] = list(info["output_flags"].values())
    if len(flags_by_output) != len(output_names):
        raise ValueError(
            f"{talib_name}: output_flags count ({len(flags_by_output)}) "
            f"!= output_names count ({len(output_names)})"
        )
    is_candlestick = _CANDLESTICK_FLAG in (info["function_flags"] or [])

    upper_idx = next(
        (i for i, flags in enumerate(flags_by_output) if _UPPER_LIMIT_FLAG in flags), None
    )
    lower_idx = next(
        (i for i, flags in enumerate(flags_by_output) if _LOWER_LIMIT_FLAG in flags), None
    )
    fill_between: list[str | None] = [None] * len(output_names)
    if upper_idx is not None and lower_idx is not None:
        fill_between[upper_idx] = output_names[lower_idx]
        fill_between[lower_idx] = output_names[upper_idx]

    plots = []
    for i, out_name in enumerate(output_names):
        out_style = style[out_name]
        scale: ScaleHint = out_style["scale"]  # type: ignore[assignment]
        default_pane: DefaultPane = out_style["default_pane"]  # type: ignore[assignment]
        kind: PlotKind = "marker" if is_candlestick else _plot_kind(flags_by_output[i])
        color_rule = out_style.get("color_rule")
        if color_rule is None and kind == "histogram":
            color_rule = "sign"
        plots.append(
            PlotSpec(
                kind=kind,
                scale=scale,
                default_pane=default_pane,
                fill_between=fill_between[i],
                color_rule=color_rule,  # type: ignore[arg-type]
                precision=out_style.get("precision"),  # type: ignore[arg-type]
                legend_format=out_style.get("legend_format"),  # type: ignore[arg-type]
            )
        )
    return tuple(plots)


def _make_lookback(talib_name: str) -> Callable[[dict[str, int]], int]:
    """실제 `TA_*_Lookback`(C 라이브러리)을 매 호출마다 실측한다.

    미지정 float 파라미터는 TA-Lib이 자체 기본값을 적용하므로(모듈 docstring
    검증됨) 여기 전달하는 `params`는 정수 파라미터만으로 충분하다.
    """

    def _lookback(params: dict[str, int]) -> int:
        function = talib_abstract.Function(talib_name, **params)  # type: ignore[attr-defined]
        return int(function.lookback)

    _lookback.__name__ = f"talib_{talib_name.lower()}_lookback"
    return _lookback


def _talib_group(name: str) -> str:
    info = talib_abstract.Function(name).info  # type: ignore[attr-defined]
    return str(info["group"])


def _all_talib_functions() -> list[str]:
    return list(talib.get_functions())  # type: ignore[no-untyped-call]


TALIB_GROUPS: dict[str, str] = {name: _talib_group(name) for name in sorted(_all_talib_functions())}


def generate_talib_specs(names: Iterable[str] | None = None) -> dict[str, IndicatorSpec]:
    """TA-Lib 메타데이터에서 `IndicatorSpec` 딕셔너리를 생성한다.

    `names`가 None이면 `talib.get_functions()`(161종) 전체, 아니면 주어진
    이름만 생성한다 — 증분 생성(부분집합)과 일괄 생성(전체) 결과가 겹치는
    이름에 대해 바이트 동일해야 한다(DoD). 미지 함수명은 거부한다(fail-closed).
    """
    known = set(_all_talib_functions())
    selected = sorted(known) if names is None else sorted(names)
    unknown = [name for name in selected if name not in known]
    if unknown:
        raise ValueError(f"unknown talib function(s): {unknown}")

    specs: dict[str, IndicatorSpec] = {}
    for name in selected:
        info = talib_abstract.Function(name).info  # type: ignore[attr-defined]
        inputs = _flatten_inputs(info["input_names"])
        params = _int_param_specs(info["parameters"])
        outputs = tuple(info["output_names"])
        scale, default_pane = _style_for_function(info["function_flags"])
        style = {out: {"scale": scale, "default_pane": default_pane} for out in outputs}
        plots = _plots_from_talib(name, outputs, style)
        specs[name] = IndicatorSpec(
            name=name,
            inputs=inputs,
            params=params,
            outputs=outputs,
            lookback=_make_lookback(name),
            plots=plots,
        )
    return specs
