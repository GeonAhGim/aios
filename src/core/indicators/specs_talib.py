"""L01 — TA-Lib 161-category catalog: auto-generated + 11 manual overrides.

Spec: L4_strategy_portfolio_backtest_v1_0.md §2.2 L01,
L4_analytics_authoring_backtest_marketplace_v1_0.md §9.9 IND-10, §9.11 IND-15.

{TALIB_SPECS} overlays on top of `generate_talib_specs()` (161 auto-generated types),
with `_MANUAL_OVERRIDES` (11 — SMA/EMA/RSI/ATR/CCI/WILLR/MFI/MACD/BBANDS/STOCH/OBV)
applied on top. Overrides only adjust display details such as scale, precision,
and histogram sign color.
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


def _ma_lookback(p: dict[str, int]) -> int:
    """SMA/EMA/CCI/WILLR/BBANDS — timeperiod - 1."""
    return p["timeperiod"] - 1


def _acc_lookback(p: dict[str, int]) -> int:
    """RSI/ATR/MFI — Wilder smoothing: timeperiod."""
    return p["timeperiod"]


def _macd_lookback(p: dict[str, int]) -> int:
    return p["slowperiod"] + p["signalperiod"] - 2


def _stoch_lookback(p: dict[str, int]) -> int:
    return p["fastk_period"] + p["slowk_period"] + p["slowd_period"] - 3


def _obv_lookback(_p: dict[str, int]) -> int:
    return 0


# scale/pane/color/precision/legend must be filled per-indicator;
# output_flags lacks these fields.
_OV: OutputStyle = {"scale": "overlay", "default_pane": "price"}
_OSC: OutputStyle = {"scale": "own", "default_pane": "separate", "precision": 2}
_OS: OutputStyle = {"scale": "own", "default_pane": "separate"}

_MANUAL_OVERRIDES: dict[str, IndicatorSpec] = {
    "SMA": IndicatorSpec(
        name="SMA", inputs=("close",), params=(_period("timeperiod", 20),),
        outputs=("value",), lookback=_ma_lookback,
        plots=_plots_from_talib("SMA", ("value",), {"value": _OV}),
    ),
    "EMA": IndicatorSpec(
        name="EMA", inputs=("close",), params=(_period("timeperiod", 20),),
        outputs=("value",), lookback=_ma_lookback,
        plots=_plots_from_talib("EMA", ("value",), {"value": _OV}),
    ),
    "RSI": IndicatorSpec(
        name="RSI", inputs=("close",), params=(_period("timeperiod", 14),),
        outputs=("value",), lookback=_acc_lookback,
        plots=_plots_from_talib("RSI", ("value",), {"value": _OSC}),
    ),
    "ATR": IndicatorSpec(
        name="ATR", inputs=("high", "low", "close"), params=(_period("timeperiod", 14),),
        outputs=("value",), lookback=_acc_lookback,
        plots=_plots_from_talib("ATR", ("value",), {"value": {**_OS, "precision": 4}}),
    ),
    "CCI": IndicatorSpec(
        name="CCI", inputs=("high", "low", "close"), params=(_period("timeperiod", 14),),
        outputs=("value",), lookback=_ma_lookback,
        plots=_plots_from_talib("CCI", ("value",), {"value": _OSC}),
    ),
    "WILLR": IndicatorSpec(
        name="WILLR", inputs=("high", "low", "close"), params=(_period("timeperiod", 14),),
        outputs=("value",), lookback=_ma_lookback,
        plots=_plots_from_talib("WILLR", ("value",), {"value": _OSC}),
    ),
    "MFI": IndicatorSpec(
        name="MFI", inputs=("high", "low", "close", "volume"),
        params=(_period("timeperiod", 14),), outputs=("value",),
        lookback=_acc_lookback,
        plots=_plots_from_talib("MFI", ("value",), {"value": _OSC}),
    ),
    "MACD": IndicatorSpec(
        name="MACD", inputs=("close",),
        params=(_period("fastperiod", 12), _period("slowperiod", 26),
                 _period("signalperiod", 9)),
        outputs=("macd", "signal", "hist"), lookback=_macd_lookback,
        plots=_plots_from_talib("MACD", ("macd", "signal", "hist"),
                                 {"macd": _OS, "signal": _OS,
                                  "hist": {**_OS, "color_rule": "sign"}}),
    ),
    "BBANDS": IndicatorSpec(
        name="BBANDS", inputs=("close",), params=(_period("timeperiod", 5),),
        outputs=("upperband", "middleband", "lowerband"),
        lookback=_ma_lookback,
        plots=_plots_from_talib("BBANDS", ("upperband", "middleband", "lowerband"),
                                 {"upperband": _OV, "middleband": _OV,
                                  "lowerband": _OV}),
    ),
    "STOCH": IndicatorSpec(
        name="STOCH", inputs=("high", "low", "close"),
        params=(_period("fastk_period", 5), _period("slowk_period", 3),
                 _period("slowd_period", 3)),
        outputs=("slowk", "slowd"), lookback=_stoch_lookback,
        plots=_plots_from_talib("STOCH", ("slowk", "slowd"),
                                 {"slowk": _OSC, "slowd": _OSC}),
    ),
    "OBV": IndicatorSpec(
        name="OBV", inputs=("close", "volume"), params=(), outputs=("value",),
        lookback=_obv_lookback,
        plots=_plots_from_talib("OBV", ("value",), {"value": _OS}),
    ),
}

TALIB_SPECS: dict[str, IndicatorSpec] = {**generate_talib_specs(), **_MANUAL_OVERRIDES}
