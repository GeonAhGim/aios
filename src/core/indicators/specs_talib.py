"""L01 — TA-Lib 161종 카탈로그: 자동 생성 + 11개 수기 오버라이드.

Spec: L4_strategy_portfolio_backtest_v1_0.md §2.2 L01,
L4_analytics_authoring_backtest_marketplace_v1_0.md §9.9 IND-10, §9.11 IND-15.

`TALIB_SPECS`는 `generate_talib_specs()`(161종 자동 생성) 위에
`_MANUAL_OVERRIDES`(11개 — SMA/EMA/RSI/ATR/CCI/WILLR/MFI/MACD/BBANDS/STOCH/OBV)를
덮어쓴 것. 오버라이드는 스케일·정밀도·히스토그램 부호 색상 등 표시 세부만 보정.
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


def _ma_lookback(p: dict[str, int]) -> int:
    """SMA/EMA/CCI/WILLR/BBANDS — timeperiod - 1."""
    return p["timeperiod"] - 1


def _acc_lookback(p: dict[str, int]) -> int:
    """RSI/ATR/MFI — Wilder 평활화: timeperiod."""
    return p["timeperiod"]


def _macd_lookback(p: dict[str, int]) -> int:
    return p["slowperiod"] + p["signalperiod"] - 2


def _stoch_lookback(p: dict[str, int]) -> int:
    return p["fastk_period"] + p["slowk_period"] + p["slowd_period"] - 3


def _obv_lookback(_p: dict[str, int]) -> int:
    return 0


# scale/페인/색상/정밀도/범례는 output_flags에 없어 지표별로 수기 채움.
_OV: dict[str, object] = {"scale": "overlay", "default_pane": "price"}
_OSC: dict[str, object] = {"scale": "own", "default_pane": "separate", "precision": 2}
_OS: dict[str, object] = {"scale": "own", "default_pane": "separate"}

_MANUAL_OVERRIDES: dict[str, IndicatorSpec] = {
    "SMA": IndicatorSpec(
        "SMA", ("close",), (_period("timeperiod", 20),),
        ("value",), _ma_lookback,
        _plots_from_talib("SMA", ("value",), {"value": _OV}),
    ),
    "EMA": IndicatorSpec(
        "EMA", ("close",), (_period("timeperiod", 20),),
        ("value",), _ma_lookback,
        _plots_from_talib("EMA", ("value",), {"value": _OV}),
    ),
    "RSI": IndicatorSpec(
        "RSI", ("close",), (_period("timeperiod", 14),),
        ("value",), _acc_lookback,
        _plots_from_talib("RSI", ("value",), {"value": _OSC}),
    ),
    "ATR": IndicatorSpec(
        "ATR", ("high", "low", "close"), (_period("timeperiod", 14),),
        ("value",), _acc_lookback,
        _plots_from_talib("ATR", ("value",), {"value": {**_OS, "precision": 4}}),
    ),
    "CCI": IndicatorSpec(
        "CCI", ("high", "low", "close"), (_period("timeperiod", 14),),
        ("value",), _ma_lookback,
        _plots_from_talib("CCI", ("value",), {"value": _OSC}),
    ),
    "WILLR": IndicatorSpec(
        "WILLR", ("high", "low", "close"), (_period("timeperiod", 14),),
        ("value",), _ma_lookback,
        _plots_from_talib("WILLR", ("value",), {"value": _OSC}),
    ),
    "MFI": IndicatorSpec(
        "MFI", ("high", "low", "close", "volume"),
        (_period("timeperiod", 14),), ("value",),
        _acc_lookback,
        _plots_from_talib("MFI", ("value",), {"value": _OSC}),
    ),
    "MACD": IndicatorSpec(
        "MACD", ("close",),
        (_period("fastperiod", 12), _period("slowperiod", 26),
         _period("signalperiod", 9)),
        ("macd", "signal", "hist"), _macd_lookback,
        _plots_from_talib("MACD", ("macd", "signal", "hist"),
                          {"macd": _OS, "signal": _OS,
                           "hist": {**_OS, "color_rule": "sign"}}),
    ),
    "BBANDS": IndicatorSpec(
        "BBANDS", ("close",), (_period("timeperiod", 5),),
        ("upperband", "middleband", "lowerband"),
        _ma_lookback,
        _plots_from_talib("BBANDS", ("upperband", "middleband", "lowerband"),
                          {"upperband": _OV, "middleband": _OV,
                           "lowerband": _OV}),
    ),
    "STOCH": IndicatorSpec(
        "STOCH", ("high", "low", "close"),
        (_period("fastk_period", 5), _period("slowk_period", 3),
         _period("slowd_period", 3)),
        ("slowk", "slowd"), _stoch_lookback,
        _plots_from_talib("STOCH", ("slowk", "slowd"),
                          {"slowk": _OSC, "slowd": _OSC}),
    ),
    "OBV": IndicatorSpec(
        "OBV", ("close", "volume"), (), ("value",),
        _obv_lookback,
        _plots_from_talib("OBV", ("value",), {"value": _OS}),
    ),
}

TALIB_SPECS: dict[str, IndicatorSpec] = {**generate_talib_specs(), **_MANUAL_OVERRIDES}
