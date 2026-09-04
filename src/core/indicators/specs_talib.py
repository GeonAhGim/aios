"""L01 — 현 `talib_adapter._SPECS` 11개를 `IndicatorSpec`으로 이전.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md §2.2 L01

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
"""
from __future__ import annotations

from src.core.indicators.spec import IndicatorSpec, ParamSpec

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


TALIB_SPECS: dict[str, IndicatorSpec] = {
    "SMA": IndicatorSpec(
        name="SMA",
        inputs=("close",),
        params=(_period("timeperiod", 20),),
        outputs=("value",),
        lookback=_ma_style_lookback,
    ),
    "EMA": IndicatorSpec(
        name="EMA",
        inputs=("close",),
        params=(_period("timeperiod", 20),),
        outputs=("value",),
        lookback=_ma_style_lookback,
    ),
    "RSI": IndicatorSpec(
        name="RSI",
        inputs=("close",),
        params=(_period("timeperiod", 14),),
        outputs=("value",),
        lookback=_accumulator_style_lookback,
    ),
    "ATR": IndicatorSpec(
        name="ATR",
        inputs=("high", "low", "close"),
        params=(_period("timeperiod", 14),),
        outputs=("value",),
        lookback=_accumulator_style_lookback,
    ),
    "CCI": IndicatorSpec(
        name="CCI",
        inputs=("high", "low", "close"),
        params=(_period("timeperiod", 14),),
        outputs=("value",),
        lookback=_ma_style_lookback,
    ),
    "WILLR": IndicatorSpec(
        name="WILLR",
        inputs=("high", "low", "close"),
        params=(_period("timeperiod", 14),),
        outputs=("value",),
        lookback=_ma_style_lookback,
    ),
    "MFI": IndicatorSpec(
        name="MFI",
        inputs=("high", "low", "close", "volume"),
        params=(_period("timeperiod", 14),),
        outputs=("value",),
        lookback=_accumulator_style_lookback,
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
    ),
    "BBANDS": IndicatorSpec(
        name="BBANDS",
        inputs=("close",),
        params=(_period("timeperiod", 5),),
        outputs=("upperband", "middleband", "lowerband"),
        lookback=_ma_style_lookback,
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
    ),
    "OBV": IndicatorSpec(
        name="OBV",
        inputs=("close", "volume"),
        params=(),
        outputs=("value",),
        lookback=_obv_lookback,
    ),
}
