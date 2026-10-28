"""L01 — Indicator specification types.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md §2.2 L01,
docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §9.11 IND-15
(ADR-2026-09-06-C D2, ADR-2026-09-06-F D1).

Pure data/type module — no I/O or computation logic. `IND-1` (indicator engine),
L07 (lookback), and L28 (series_cache) depend 1:1 on this contract, so do not
extend fields beyond what is defined here. CH-15 (front-end renderer) parses
`PlotSpec` as SSOT, so changing field names breaks the front end.
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
    """One indicator parameter's name and allowed range."""

    name: str
    min: int
    max: int
    default: int


@dataclass(frozen=True)
class PlotSpec:
    """Declarative display contract for one indicator output (ADR-2026-09-06-C D2).

    The `kind`/`fill_between` defaults are derived from TA-Lib `output_flags`
    (ADR-2026-09-06-F D1) — derivation logic lives in `specs_talib.py`.
    This class itself is a pure data container for derived values, and
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
    """Computation contract for one indicator: input series, parameters, outputs,
    lookback, display contract.

    `plots` is a required field mapping 1:1 to `outputs` — indicators cannot be
    registered without a display contract (`PlotSpec`) (fail-closed, IND-15 DoD).
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
            if plot.fill_between is not None and plot.fill_between not in self.outputs:
                raise ValueError(
                    f"{self.name}: fill_between references unknown output "
                    f"{plot.fill_between!r} (outputs={self.outputs!r})"
                )
