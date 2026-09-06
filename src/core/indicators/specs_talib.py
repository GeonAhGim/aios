"""L01 — 현 `talib_adapter._SPECS` 11개를 `IndicatorSpec`으로 이전.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md §2.2 L01,
docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §9.11 IND-15

lookback 값은 결정 노트의 산식(`timeperiod`, `slowperiod+signalperiod-1`,
`fastk_period+slowk_period+slowd_period-2`, `OBV=1`)이 아니라
`talib.abstract.Function(name).lookback`(TA-Lib C 라이브러리의 실제
`TA_*_Lookback`)로 실측 정정한 값을 쓴다 — SMA/EMA/CCI/WILLR/BBANDS는
`timeperiod - 1`(이동평균 계열은 첫 값 계산에 timeperiod개 중 마지막 1개는
NaN이 아니므로), MACD는 `slowperiod + signalperiod - 2`(EMA 두 단 합성),
STOCH는 `fastk+slowk+slowd - 3`, OBV는 `0`(첫 bar부터 값이 존재, NaN 없음)이
실측값과 정확히 일치한다. 이 leaf의 DoD(`test_registry.py -k specs`)가
"하드코딩 기대값이 아니라 실측 NaN 개수와 일치"를 요구하므로 결정 노트의
산식을 그대로 베끼면 테스트가 깨진다 — TA-Lib 실측을 우선한다.

talib_adapter.py는 이 leaf에서 수정하지 않는다(L03 몫). 기존 `_SPECS`와
이 모듈은 병존한다.

`PlotSpec.kind`/`fill_between`은 IND-15(ADR-2026-09-06-F D1)에 따라 손으로
적지 않고 `talib.abstract.Function(name).info["output_flags"]`에서
`_plots_from_talib`가 자동 도출한다 — `Line`→`line`, `Dashed Line`→`line`,
`Histogram`→`histogram`, upper/lower limit 쌍→`band`+`fill_between`. 스케일·
페인·색상 규칙·정밀도·범례 서식은 output_flags에 없는 정보라 지표별로
`_plots_from_talib(...)` 호출 시 style 인자로 사람이 채운다.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence

from talib import abstract as talib_abstract

from src.core.indicators.spec import (
    DefaultPane,
    IndicatorSpec,
    ParamSpec,
    PlotKind,
    PlotSpec,
    ScaleHint,
)

_MIN_PERIOD = 2
_MAX_PERIOD = 500

_HISTOGRAM_FLAG = "Histogram"
_UPPER_LIMIT_FLAG = "Values represent an upper limit"
_LOWER_LIMIT_FLAG = "Values represent a lower limit"


def _period(name: str, default: int) -> ParamSpec:
    return ParamSpec(name=name, min=_MIN_PERIOD, max=_MAX_PERIOD, default=default)


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

    `output_names`는 talib 원본 출력 순서와 1:1 대응하는(이름만 바꾼) 이 모듈의
    출력 이름이어야 한다 — 순서가 어긋나면 kind/fill_between이 잘못 배정된다.
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
        plots.append(
            PlotSpec(
                kind=_plot_kind(flags_by_output[i]),
                scale=scale,
                default_pane=default_pane,
                fill_between=fill_between[i],
                color_rule=out_style.get("color_rule"),  # type: ignore[arg-type]
                precision=out_style.get("precision"),  # type: ignore[arg-type]
                legend_format=out_style.get("legend_format"),  # type: ignore[arg-type]
            )
        )
    return tuple(plots)


def _ma_style_lookback(params: dict[str, int]) -> int:
    """SMA/EMA/CCI/WILLR/BBANDS — 창 안의 마지막 bar에서 값이 나오므로 timeperiod - 1."""
    return params["timeperiod"] - 1


def _accumulator_style_lookback(params: dict[str, int]) -> int:
    """RSI/ATR/MFI — Wilder 평활화가 timeperiod개 변화량을 소비하므로 timeperiod."""
    return params["timeperiod"]


def _macd_lookback(params: dict[str, int]) -> int:
    return params["slowperiod"] + params["signalperiod"] - 2


def _stoch_lookback(params: dict[str, int]) -> int:
    return params["fastk_period"] + params["slowk_period"] + params["slowd_period"] - 3


def _obv_lookback(_params: dict[str, int]) -> int:
    return 0


# 스케일/페인/색상/정밀도/범례는 output_flags에 없는 정보라 지표별로 사람이 채운다
# (kind/fill_between만 output_flags에서 자동 도출 — ADR-2026-09-06-F D1).
_OVERLAY_PRICE: dict[str, object] = {"scale": "overlay", "default_pane": "price"}
_OSCILLATOR_0_100: dict[str, object] = {
    "scale": "own",
    "default_pane": "separate",
    "precision": 2,
}
_OWN_SEPARATE: dict[str, object] = {"scale": "own", "default_pane": "separate"}

TALIB_SPECS: dict[str, IndicatorSpec] = {
    "SMA": IndicatorSpec(
        name="SMA",
        inputs=("close",),
        params=(_period("timeperiod", 20),),
        outputs=("value",),
        lookback=_ma_style_lookback,
        plots=_plots_from_talib("SMA", ("value",), {"value": _OVERLAY_PRICE}),
    ),
    "EMA": IndicatorSpec(
        name="EMA",
        inputs=("close",),
        params=(_period("timeperiod", 20),),
        outputs=("value",),
        lookback=_ma_style_lookback,
        plots=_plots_from_talib("EMA", ("value",), {"value": _OVERLAY_PRICE}),
    ),
    "RSI": IndicatorSpec(
        name="RSI",
        inputs=("close",),
        params=(_period("timeperiod", 14),),
        outputs=("value",),
        lookback=_accumulator_style_lookback,
        plots=_plots_from_talib("RSI", ("value",), {"value": _OSCILLATOR_0_100}),
    ),
    "ATR": IndicatorSpec(
        name="ATR",
        inputs=("high", "low", "close"),
        params=(_period("timeperiod", 14),),
        outputs=("value",),
        lookback=_accumulator_style_lookback,
        plots=_plots_from_talib(
            "ATR", ("value",), {"value": {**_OWN_SEPARATE, "precision": 4}}
        ),
    ),
    "CCI": IndicatorSpec(
        name="CCI",
        inputs=("high", "low", "close"),
        params=(_period("timeperiod", 14),),
        outputs=("value",),
        lookback=_ma_style_lookback,
        plots=_plots_from_talib("CCI", ("value",), {"value": _OSCILLATOR_0_100}),
    ),
    "WILLR": IndicatorSpec(
        name="WILLR",
        inputs=("high", "low", "close"),
        params=(_period("timeperiod", 14),),
        outputs=("value",),
        lookback=_ma_style_lookback,
        plots=_plots_from_talib("WILLR", ("value",), {"value": _OSCILLATOR_0_100}),
    ),
    "MFI": IndicatorSpec(
        name="MFI",
        inputs=("high", "low", "close", "volume"),
        params=(_period("timeperiod", 14),),
        outputs=("value",),
        lookback=_accumulator_style_lookback,
        plots=_plots_from_talib("MFI", ("value",), {"value": _OSCILLATOR_0_100}),
    ),
    "MACD": IndicatorSpec(
        name="MACD",
        inputs=("close",),
        params=(
            _period("fastperiod", 12),
            _period("slowperiod", 26),
            _period("signalperiod", 9),
        ),
        outputs=("macd", "signal", "hist"),
        lookback=_macd_lookback,
        plots=_plots_from_talib(
            "MACD",
            ("macd", "signal", "hist"),
            {
                "macd": _OWN_SEPARATE,
                "signal": _OWN_SEPARATE,
                "hist": {**_OWN_SEPARATE, "color_rule": "sign"},
            },
        ),
    ),
    "BBANDS": IndicatorSpec(
        name="BBANDS",
        inputs=("close",),
        params=(_period("timeperiod", 5),),
        outputs=("upperband", "middleband", "lowerband"),
        lookback=_ma_style_lookback,
        plots=_plots_from_talib(
            "BBANDS",
            ("upperband", "middleband", "lowerband"),
            {
                "upperband": _OVERLAY_PRICE,
                "middleband": _OVERLAY_PRICE,
                "lowerband": _OVERLAY_PRICE,
            },
        ),
    ),
    "STOCH": IndicatorSpec(
        name="STOCH",
        inputs=("high", "low", "close"),
        params=(
            _period("fastk_period", 5),
            _period("slowk_period", 3),
            _period("slowd_period", 3),
        ),
        outputs=("slowk", "slowd"),
        lookback=_stoch_lookback,
        plots=_plots_from_talib(
            "STOCH",
            ("slowk", "slowd"),
            {"slowk": _OSCILLATOR_0_100, "slowd": _OSCILLATOR_0_100},
        ),
    ),
    "OBV": IndicatorSpec(
        name="OBV",
        inputs=("close", "volume"),
        params=(),
        outputs=("value",),
        lookback=_obv_lookback,
        plots=_plots_from_talib("OBV", ("value",), {"value": _OWN_SEPARATE}),
    ),
}
