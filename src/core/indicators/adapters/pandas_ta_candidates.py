"""IND-11 — pandas-ta-classic candidate indicators (DPO, MASSI, COPPOCK).

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §9.9 IND-11,
ADR-2026-09-09-A, docs/design/INDICATOR_OSS_EVAL.md §5·§6.

The *catalog* of pandas-ta-classic indicators this codebase knows how to
compute: `IndicatorSpec`s plus the calculator per name. Whether a candidate is
actually registered is decided in `pandas_ta_bridge.py` against the installed
TA-Lib (`select_registered`) — this module holds no TA-Lib policy, so the two
change axes (adding a candidate vs. deciding overlap) stay in separate files
(ADR-2026-09-10-C Decision 2).
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
import pandas_ta_classic as pta

from src.core.indicators.registry import IndicatorError
from src.core.indicators.spec import IndicatorSpec, ParamSpec, PlotSpec

__all__ = [
    "CALCULATORS",
    "CANDIDATE_INDICATORS",
    "CANDIDATE_SPECS",
]


# --- candidate indicators: DPO, MASSI, COPPOCK ------------------------------
#
# All three take only OHLC columns, are single-output, and are causal by
# construction (see each helper below) -- the three properties that make the
# IND-7g-style three-way cross-verification in `tests/.../test_pandas_ta_
# bridge.py` meaningful: (1) pandas-ta-classic's own call, (2) an independent
# numpy re-derivation of the published formula, (3) for MASSI specifically,
# its EMA sub-step cross-checked against TA-Lib's EMA directly (pandas-ta-
# classic's own EMA implementation seeds itself from an SMA to match TA-Lib's
# EMA lookback behaviour -- see `pandas_ta_classic/overlap/ema.py` -- so this
# is a real second-oracle check, not just re-running the same formula twice).


def _period(name: str, default: int, *, minimum: int = 2, maximum: int = 500) -> ParamSpec:
    return ParamSpec(name=name, min=minimum, max=maximum, default=default)


def _dpo_lookback(params: dict[str, int]) -> int:
    length = params["length"]
    shift = length // 2 + 1
    return length - 1 + shift


def _massi_lookback(params: dict[str, int]) -> int:
    fast, slow = params["fast"], params["slow"]
    # Double EMA(fast) needs ~2*fast bars to leave the SMA seed window, then a
    # rolling sum over `slow` bars on top -- deliberately generous (fail-closed
    # toward "insufficient data" rather than toward an unstable early value).
    return 2 * fast + slow


def _coppock_lookback(params: dict[str, int]) -> int:
    length, fast, slow = params["length"], params["fast"], params["slow"]
    return max(fast, slow) + length - 1


def _calc_dpo(columns: dict[str, np.ndarray[Any, Any]], params: dict[str, int]) -> pd.Series:
    close = pd.Series(columns["close"])
    result = pta.dpo(close, length=params["length"], centered=False, lookahead=False)
    if result is None:
        raise IndicatorError("STRATEGY_INDICATOR_COMPUTE_FAILED")
    return result


def _calc_massi(columns: dict[str, np.ndarray[Any, Any]], params: dict[str, int]) -> pd.Series:
    high = pd.Series(columns["high"])
    low = pd.Series(columns["low"])
    result = pta.massi(high, low, fast=params["fast"], slow=params["slow"])
    if result is None:
        raise IndicatorError("STRATEGY_INDICATOR_COMPUTE_FAILED")
    return result


def _calc_coppock(columns: dict[str, np.ndarray[Any, Any]], params: dict[str, int]) -> pd.Series:
    close = pd.Series(columns["close"])
    result = pta.coppock(
        close, length=params["length"], fast=params["fast"], slow=params["slow"]
    )
    if result is None:
        raise IndicatorError("STRATEGY_INDICATOR_COMPUTE_FAILED")
    return result


def _osc_plot(precision: int) -> PlotSpec:
    return PlotSpec(kind="line", scale="own", default_pane="separate", precision=precision)


CANDIDATE_SPECS: dict[str, IndicatorSpec] = {
    "dpo": IndicatorSpec(
        name="dpo",
        inputs=("close",),
        params=(_period("length", 20),),
        outputs=("value",),
        lookback=_dpo_lookback,
        plots=(_osc_plot(4),),
    ),
    "massi": IndicatorSpec(
        name="massi",
        inputs=("high", "low"),
        params=(
            _period("fast", 9, minimum=2, maximum=100),
            _period("slow", 25, minimum=2, maximum=200),
        ),
        outputs=("value",),
        lookback=_massi_lookback,
        plots=(_osc_plot(4),),
    ),
    "coppock": IndicatorSpec(
        name="coppock",
        inputs=("close",),
        params=(
            _period("length", 10, minimum=2, maximum=100),
            _period("fast", 11, minimum=2, maximum=200),
            _period("slow", 14, minimum=2, maximum=200),
        ),
        outputs=("value",),
        lookback=_coppock_lookback,
        plots=(_osc_plot(4),),
    ),
}

CALCULATORS: dict[str, Any] = {
    "dpo": _calc_dpo,
    "massi": _calc_massi,
    "coppock": _calc_coppock,
}

CANDIDATE_INDICATORS: tuple[str, ...] = tuple(sorted(CANDIDATE_SPECS))
