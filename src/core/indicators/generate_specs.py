"""IND-10 — Auto-generate `IndicatorSpec` metadata for 161 TA-Lib functions.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §9.9 IND-10
(Precedes IND-9·IND-1, ADR-2026-09-06-F D1).

Iterates `talib.get_functions()` (161 types) x `abstract.Function(name).info` to
**generate** specs (manual writing prohibited) — group→category (`TALIB_GROUPS`),
parameters(defaults)→integer parameter range rules, `.lookback`→measured lookback,
output_names→output contract, output_flags/function_flags→`PlotSpec` defaults.

Floating-point parameters (nbdevup, acceleration, penetration, etc., 14 of 161 types)
are not exposed as `ParamSpec` at this generation stage — `IndicatorRegistry`/`engine/
incremental.py`·`engine/vectorized.py` (IND-1, outside this leaf) share parameters
as a single type `dict[str, int]`, so exposing float as a first-class type would
require widening that contract to `int | float` everywhere — a change that spills
outside this leaf's file scope (`talib_adapter.py`·`registry.py`·`specs_talib.py`),
so we do not do it. Instead we rely on TA-Lib's own defaults
(`talib.abstract.Function(name, **only_int_params)` — when float params are omitted,
TA-Lib applies its own defaults; we verify the value/lookback is byte-identical to
explicitly passing them). Of the "integer period 1~2000 · deviation 0.1~10" rules,
the deviation side is implemented only as a pure function `_deviation_range()`
(the rule itself exists in the decision note), and is not yet wired into
`ParamSpec` — wire it later if needed after IND-12 (registry three-layering).
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
    """Decision note rule "deviation 0.1~10" (pure function). Not yet wired into
    `ParamSpec` — see module docstring. If the default falls outside the rule range
    (e.g. SAR acceleration), symmetrically expand to include the default
    (always guarantees min <= default <= max)."""
    if _DEVIATION_MIN <= default <= _DEVIATION_MAX:
        return _DEVIATION_MIN, _DEVIATION_MAX
    if default <= 0:
        return 0.0, _DEVIATION_MAX
    return min(_DEVIATION_MIN, default), max(_DEVIATION_MAX, default)


def _flatten_inputs(input_names: Mapping[str, object]) -> tuple[str, ...]:
    """Flatten `abstract.Function.info["input_names"]` into a tuple of candle field names.

    If the value is a `list` (e.g. `{'prices': ['high','low','close']}`), expand it;
    if it's a string (e.g. `{'price0': 'high'}`), append it as-is — order must
    preserve the insertion order from info so positional argument order matches
    when calling the talib function.
    """
    flat: list[str] = []
    for value in input_names.values():
        if isinstance(value, list):
            flat.extend(value)
        else:
            flat.append(str(value))
    return tuple(flat)


def _int_param_specs(parameters: Mapping[str, object]) -> tuple[ParamSpec, ...]:
    """Expose only integer parameters as `ParamSpec` (floats — see module docstring).

    matype family: 0~8 (talib.MA_Type ordinal); other integers: period rule 1~2000.
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
    """Determine scale and pane from `function_flags` (finer talib signal) instead of group.

    "candlestick" and "same scale as input" both draw as price overlay (`overlay`/`price`)
    — the latter covers SMA/BBANDS (Overlap Studies, Price Transform), the former
    covers 61 Pattern Recognition types (scale for drawing markers matches price).
    The rest (momentum, volume, volatility, cycle, math families) get their own
    scale and separate pane.
    """
    flags = function_flags or []
    if _CANDLESTICK_FLAG in flags or _SAME_SCALE_FLAG in flags:
        return "overlay", "price"
    return "own", "separate"


def _plot_kind(flags: Sequence[str]) -> PlotKind:
    """Map one TA-Lib output_flag to PlotSpec.kind (ADR-2026-09-06-F D1)."""
    if _HISTOGRAM_FLAG in flags:
        return "histogram"
    if _UPPER_LIMIT_FLAG in flags or _LOWER_LIMIT_FLAG in flags:
        return "band"
    return "line"  # Both Line and Dashed Line map to "line"


def _plots_from_talib(
    talib_name: str,
    output_names: tuple[str, ...],
    style: Mapping[str, Mapping[str, object]],
) -> tuple[PlotSpec, ...]:
    """Derive kind/fill_between from actual output_flags of `talib_name` to build PlotSpec.

    `output_names` must be this module's output names that correspond 1:1 with talib's
    original output order (only names may differ) — if order is off, kind/fill_between
    will be assigned incorrectly. Candlestick functions have output_flags of `Line` but
    are actually markers on price, so force `kind="marker"`. Histogram outputs naturally
    split color by sign, so if `color_rule` is absent from style, auto-fill "sign".
    """
    # talib/abstract.pyi declares only individual indicator functions and does not
    # declare the runtime-only `Function` constructor class (talib package stub
    # limitation) — the values actually exist at runtime.
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
    """Measure actual `TA_*_Lookback` (C library) on every call.

    Unspecified float params are applied by TA-Lib with its own defaults (verified
    in module docstring), so `params` passed here only need integer parameters.
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
    """Generate `IndicatorSpec` dict from TA-Lib metadata.

    If `names` is None, generate all `talib.get_functions()` (161 types); otherwise
    generate only the given names — incremental (subset) and batch (full) results
    must be byte-identical for overlapping names (DoD). Reject unknown function
    names (fail-closed).
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
