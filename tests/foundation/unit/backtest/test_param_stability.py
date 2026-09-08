"""Unit tests for `backtest/domain/param_stability.py` -- task-2386 L33 DoD (e)."""
from decimal import Decimal

import pytest

from src.foundation.backtest.domain.param_stability import (
    ParamGrid,
    ParamStabilityError,
    stability_score,
)

AXES = {"p": [5, 10, 15, 20, 25]}


def test_sharp_peak_is_isolated() -> None:
    grid = ParamGrid(axes=AXES)
    metric_by_point = {
        (5,): Decimal("0.05"),
        (10,): Decimal("0.1"),
        (15,): Decimal("1.0"),
        (20,): Decimal("0.1"),
        (25,): Decimal("0.05"),
    }
    report = stability_score(grid, metric_by_point)
    assert report.best == (15,)
    assert report.neighbor_mean == Decimal("0.1")
    assert report.isolated is True


def test_gentle_plateau_is_not_isolated() -> None:
    grid = ParamGrid(axes=AXES)
    metric_by_point = {
        (5,): Decimal("0.8"),
        (10,): Decimal("0.9"),
        (15,): Decimal("1.0"),
        (20,): Decimal("0.9"),
        (25,): Decimal("0.8"),
    }
    report = stability_score(grid, metric_by_point)
    assert report.best == (15,)
    assert report.neighbor_mean == Decimal("0.9")
    assert report.isolated is False


def test_grid_smaller_than_four_hard_fails() -> None:
    with pytest.raises(ParamStabilityError):
        ParamGrid(axes={"p": [5, 10]})


def test_missing_metric_point_is_rejected() -> None:
    grid = ParamGrid(axes=AXES)
    incomplete = {(5,): Decimal("0.1"), (10,): Decimal("0.1"), (15,): Decimal("1.0")}
    with pytest.raises(ParamStabilityError):
        stability_score(grid, incomplete)


def test_non_ascending_axis_is_rejected() -> None:
    with pytest.raises(ParamStabilityError):
        ParamGrid(axes={"p": [5, 20, 10, 15, 25]})
