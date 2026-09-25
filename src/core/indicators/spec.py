"""L01 — Indicator specification types.

Spec: L4_strategy_portfolio_backtest_v1_0.md §2.2 L01,
L4_analytics_authoring_backtest_marketplace_v1_0.md §9.11 IND-15.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, get_args

REGISTRY_VERSION = "ind-v1"

PlotKind = Literal["line", "histogram", "area", "band", "cloud", "marker"]
ScaleHint = Literal["own", "overlay", "percent", "log", "inverted"]
DefaultPane = Literal["price", "separate"]
_VALID_PLOT_KINDS = frozenset(get_args(PlotKind))
_VALID_SCALE_HINTS = frozenset(get_args(ScaleHint))
_VALID_DEFAULT_PANES = frozenset(get_args(DefaultPane))

@dataclass(frozen=True)
class ParamSpec:
    name: str
    min: int
    max: int
    default: int


@dataclass(frozen=True)
class PlotSpec:
    """Declarative display contract for one indicator output.

    The `kind`/`fill_between` defaults are derived from TA-Lib `output_flags` (ADR-2026-09-06-F D1).
    `__post_init__` performs fail-closed validation only.
    """
    kind: PlotKind
    scale: ScaleHint
    default_pane: DefaultPane
    fill_between: str | None = None
    color_rule: str | None = None
    precision: int | None = None
    legend_format: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in _VALID_PLOT_KINDS:
            raise ValueError(f"unknown PlotSpec.kind: {self.kind!r}")
        if self.scale not in _VALID_SCALE_HINTS:
            raise ValueError(f"unknown PlotSpec.scale: {self.scale!r}")
        if self.default_pane not in _VALID_DEFAULT_PANES:
            raise ValueError(f"unknown PlotSpec.default_pane: {self.default_pane!r}")
        if self.precision is not None and self.precision < 0:
            raise ValueError(f"PlotSpec.precision must be >= 0: {self.precision!r}")


@dataclass(frozen=True)
class IndicatorSpec:
    """Computation contract for one indicator: inputs, params, outputs, lookback, display.

    `plots` maps 1:1 to `outputs`. Registration without PlotSpec is rejected (fail-closed, IND-15).
    """
    name: str
    inputs: tuple[str, ...]
    params: tuple[ParamSpec, ...]
    outputs: tuple[str, ...]
    lookback: Callable[[dict[str, int]], int]
    plots: tuple[PlotSpec, ...]
    causal: bool = True

    def __post_init__(self) -> None:
        if len(self.plots) != len(self.outputs):
            raise ValueError(
                f"{self.name}: PlotSpec count ({len(self.plots)}) "
                f"!= outputs count ({len(self.outputs)})"
            )
        for plot in self.plots:
            if plot.fill_between and plot.fill_between not in self.outputs:
                raise ValueError(
                    f"{self.name}: fill_between {plot.fill_between!r} "
                    f"not in outputs {self.outputs!r}"
                )
