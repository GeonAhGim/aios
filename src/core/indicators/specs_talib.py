"""L01 — TA-Lib 161종 카탈로그: 자동 생성 + 11개 수기 오버라이드.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md §2.2 L01,
docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §9.9 IND-10,
§9.11 IND-15 (ADR-2026-09-06-F D1).

`TALIB_SPECS` overlays on top of `generate_talib_specs()` (one auto-generated spec per
`talib.get_functions()` entry, with group→category, integer param ranges, measured
lookback, auto-derived PlotSpec from output_flags), with `_MANUAL_OVERRIDES` (11 —
SMA/EMA/RSI/ATR/CCI/WILLR/MFI/MACD/BBANDS/STOCH/OBV) applied on top. Overrides only
correct display details the auto-generation cannot know (oscillator 0–100 scale,
precision, MACD histogram sign color, etc.) — the lookback and
PlotSpec.kind/fill_between derivation logic uses the same `_plots_from_talib` as
auto-generation (no duplication).

The 150 non-overridden specs also always carry PlotSpec via
`_style_for_function` (group signal) filling default scale/default_pane and
output_flags auto-derived kind/fill_between (IND-15 fail-closed contract,
validated by `IndicatorSpec.__post_init__`).
"""

from __future__ import annotations

from src.core.indicators.generate_specs import TALIB_GROUPS as TALIB_GROUPS
from src.core.indicators.generate_specs import OutputStyle, generate_talib_specs
from src.core.indicators.generate_specs import _plot_kind as _plot_kind
from src.core.indicators.generate_specs import _plots_from_talib as _plots_from_talib
from src.core.indicators.spec import IndicatorSpec, ParamSpec

__all__ = ["TALIB_GROUPS", "TALIB_SPECS"]

_MIN_PERIOD = 2
_MAX_PERIOD = 500


def _period(name: str, default: int) -> ParamSpec:
    return ParamSpec(name=name, min=_MIN_PERIOD, max=_MAX_PERIOD, default=default)


def _ma_style_lookback(params: dict[str, int]) -> int:
    """SMA/EMA/CCI/WILLR/BBANDS — value at last bar inside window, so timeperiod - 1."""
    return params["timeperiod"] - 1


def _accumulator_style_lookback(params: dict[str, int]) -> int:
    """RSI/ATR/MFI — Wilder smoothing consumes timeperiod change amounts, so timeperiod."""
    return params["timeperiod"]


def _macd_lookback(params: dict[str, int]) -> int:
    return params["slowperiod"] + params["signalperiod"] - 2


def _stoch_lookback(params: dict[str, int]) -> int:
    return params["fastk_period"] + params["slowk_period"] + params["slowd_period"] - 3


def _obv_lookback(_params: dict[str, int]) -> int:
    return 0


# scale/pane/color/precision/legend must be filled per-indicator;
# (kind/fill_between only is auto-derived from output_flags — ADR-2026-09-06-F D1).
_OVERLAY_PRICE: OutputStyle = {"scale": "overlay", "default_pane": "price"}
_OSCILLATOR_0_100: OutputStyle = {"scale": "own", "default_pane": "separate", "precision": 2}
_OWN_SEPARATE: OutputStyle = {"scale": "own", "default_pane": "separate"}

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
        plots=_plots_from_talib("ATR", ("value",), {"value": {**_OWN_SEPARATE, "precision": 4}}),
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
