"""IND-10 — Parameter range policy for auto-generated TA-Lib `IndicatorSpec`s.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §9.9 IND-10.

Split out of `generate_specs.py` (ADR-2026-09-10-C Decision 2: split by
responsibility): the *range rules* applied to TA-Lib parameters (integer
period 1~2000, MA type 0~max of the installed `talib.MA_Type`, deviation
0.1~10) change independently of how specs are assembled from
`abstract.Function(name).info`. Nothing here is hard-coded to a TA-Lib
version — `MATYPE_MAX` is read from the installed library at import so a
newer function's own default (e.g. KDJ on TA-Lib 0.6.x) is always accepted.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

import talib

from src.core.indicators.spec import ParamSpec

__all__ = [
    "MATYPE_MAX",
    "deviation_range",
    "int_param_specs",
    "matype_max",
]

_PERIOD_MIN = 1
_PERIOD_MAX = 2000
_MATYPE_MIN = 0


def matype_max() -> int:
    """Largest `talib.MA_Type` ordinal of the installed library.

    The MA type catalog grows with the C library (0.4: SMA(0)..T3(8); 0.6 adds
    HMA(9)..RMA(13), which KDJ uses as its default). Hard-coding the upper bound
    would reject a newer function's own default (`STRATEGY_PARAM_OUT_OF_RANGE`),
    so it is derived from the enum members TA-Lib actually exposes.
    """
    # talib's stubs do not re-export `MA_Type` from the package root (the same
    # stub limitation as `abstract.Function` below) -- it exists at runtime.
    # `cast(Any, ...)` (vs. an attr-defined suppression comment) avoids
    # growing the PLT-40 type-ignore ratchet.
    ma_type = cast(Any, talib).MA_Type
    ordinals = [
        value
        for attr in dir(ma_type)
        if not attr.startswith("_") and isinstance(value := getattr(ma_type, attr), int)
    ]
    if not ordinals:
        raise ValueError("talib.MA_Type exposes no integer members")
    return max(ordinals)


MATYPE_MAX = matype_max()
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


def deviation_range(default: float) -> tuple[float, float]:
    """Decision note rule "deviation 0.1~10" (pure function). Not yet wired into
    `ParamSpec` — see `generate_specs` module docstring. If the default falls outside the rule range
    (e.g. SAR acceleration), symmetrically expand to include the default
    (always guarantees min <= default <= max)."""
    if _DEVIATION_MIN <= default <= _DEVIATION_MAX:
        return _DEVIATION_MIN, _DEVIATION_MAX
    if default <= 0:
        return 0.0, _DEVIATION_MAX
    return min(_DEVIATION_MIN, default), max(_DEVIATION_MAX, default)


def int_param_specs(parameters: Mapping[str, object]) -> tuple[ParamSpec, ...]:
    """Expose only integer parameters as `ParamSpec` (floats: see `generate_specs`
    module docstring).

    matype family: 0~`MATYPE_MAX` (installed `talib.MA_Type`); other integers:
    period rule 1~2000.
    """
    specs: list[ParamSpec] = []
    for name, default in parameters.items():
        if isinstance(default, float):
            continue
        if not isinstance(default, int):
            continue
        if name in _MATYPE_PARAM_NAMES:
            specs.append(ParamSpec(name=name, min=_MATYPE_MIN, max=MATYPE_MAX, default=default))
        else:
            specs.append(ParamSpec(name=name, min=_PERIOD_MIN, max=_PERIOD_MAX, default=default))
    return tuple(specs)
