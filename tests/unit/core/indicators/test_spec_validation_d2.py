"""D2 evidence for L01 IndicatorSpec/PlotSpec fail-closed validation (IND-15)."""
from __future__ import annotations

import time

import pytest

from src.core.indicators.spec import IndicatorSpec, ParamSpec, PlotSpec
from src.core.indicators.specs_talib import TALIB_SPECS


def _plot(fill_between: str | None) -> PlotSpec:
    return PlotSpec(kind="band", scale="overlay", default_pane="price", fill_between=fill_between)


def _spec(plots: tuple[PlotSpec, ...], outputs: tuple[str, ...] = ("a", "b")) -> IndicatorSpec:
    return IndicatorSpec(
        name="X",
        inputs=("close",),
        params=(ParamSpec(name="timeperiod", min=2, max=500, default=5),),
        outputs=outputs,
        lookback=lambda p: p["timeperiod"] - 1,
        plots=plots,
    )


def test_empty_string_fill_between_rejected() -> None:
    # Truthiness check would let "" through; `is not None` must reject it.
    with pytest.raises(ValueError, match="fill_between"):
        _spec((_plot(""), _plot(None)))


def test_unknown_fill_between_rejected() -> None:
    with pytest.raises(ValueError, match="unknown output"):
        _spec((_plot("zzz"), _plot(None)))


def test_plot_count_mismatch_rejected() -> None:
    with pytest.raises(ValueError, match="PlotSpec count"):
        _spec((_plot(None),))


def test_negative_precision_rejected() -> None:
    with pytest.raises(ValueError, match="precision"):
        PlotSpec(kind="line", scale="own", default_pane="separate", precision=-1)


def test_failure_injection_bad_kind_rejected() -> None:
    with pytest.raises(ValueError, match="kind"):
        PlotSpec(kind="bogus", scale="own", default_pane="separate")  # type: ignore[arg-type]


def test_valid_fill_between_accepted() -> None:
    assert _spec((_plot("b"), _plot("a"))).outputs == ("a", "b")


def test_catalog_validation_throughput() -> None:
    start = time.perf_counter()
    for spec in TALIB_SPECS.values():
        spec.__post_init__()
    assert time.perf_counter() - start < 1.0
