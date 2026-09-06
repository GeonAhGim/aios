"""L01 — TA-Lib 161종 카탈로그: 자동 생성 + 11개 수기 오버라이드.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md §2.2 L01,
docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §9.9 IND-10,
§9.11 IND-15 (ADR-2026-09-06-F D1).

`TALIB_SPECS`는 `generate_specs.generate_talib_specs()`(161종 자동 생성,
group→카테고리·정수 파라미터 범위·실측 lookback·output_flags 자동 PlotSpec)
위에 `_MANUAL_OVERRIDES`(이 leaf 이전부터 있던 11개 — SMA/EMA/RSI/ATR/CCI/
WILLR/MFI/MACD/BBANDS/STOCH/OBV)를 덮어쓴 것이다. 오버라이드는 자동 생성이
모를 수밖에 없는 정보(오실레이터 0~100 스케일·정밀도·MACD 히스토그램 부호
색상 등 표시 세부)만 손으로 보정한다 — lookback·PlotSpec.kind/fill_between
도출 로직 자체는 자동 생성과 같은 `_plots_from_talib`를 그대로 쓴다(중복 금지).

오버라이드 없는 150종도 `_style_for_function`(group 신호)이 채운 기본
scale/default_pane과 output_flags 자동 도출 kind/fill_between으로 PlotSpec을
반드시 갖는다(IND-15 fail-closed 계약, `IndicatorSpec.__post_init__`이 검증).
"""
from __future__ import annotations

from src.core.indicators.generate_specs import TALIB_GROUPS as TALIB_GROUPS
from src.core.indicators.generate_specs import _plot_kind as _plot_kind
from src.core.indicators.generate_specs import _plots_from_talib as _plots_from_talib
from src.core.indicators.generate_specs import generate_talib_specs
from src.core.indicators.spec import IndicatorSpec, ParamSpec

__all__ = ["TALIB_GROUPS", "TALIB_SPECS"]

_MIN_PERIOD = 2
_MAX_PERIOD = 500


def _period(name: str, default: int) -> ParamSpec:
    return ParamSpec(name=name, min=_MIN_PERIOD, max=_MAX_PERIOD, default=default)


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

_MANUAL_OVERRIDES: dict[str, IndicatorSpec] = {
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

TALIB_SPECS: dict[str, IndicatorSpec] = {**generate_talib_specs(), **_MANUAL_OVERRIDES}
