"""L33 -- `backtest/domain/param_stability.py`: parameter-grid neighbor-performance
dispersion and best-point isolation detection.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md #9 L33 (contract: #2 table
`param_stability.py` row). Pure domain module -- no I/O, no randomness, stdlib only.

A best point that only performs well in isolation (its immediate neighbors on the
grid perform far worse) is a classic overfitting symptom -- a robust parameter choice
should keep performing reasonably well on nearby grid points. `stability_score` finds
the best point, averages the metric over its immediate axis-wise neighbors, and flags
`isolated=True` when that neighbor average drops far below the best point's own value.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from itertools import product

__all__ = [
    "NEIGHBOR_RATIO_ISOLATION_THRESHOLD",
    "ParamGrid",
    "ParamStabilityError",
    "ParameterStabilityReport",
    "Point",
    "stability_score",
]

Point = tuple[int, ...]

MIN_GRID_SIZE = 4
NEIGHBOR_RATIO_ISOLATION_THRESHOLD = Decimal("0.5")


class ParamStabilityError(ValueError):
    """Fail-closed rejection for `param_stability.py` -- an unreproducible grid
    (size < 4) or a `metric_by_point` that doesn't cover every grid point."""


@dataclass(frozen=True, slots=True)
class ParamGrid:
    """`axes` maps each axis name to its candidate values, strictly ascending and
    duplicate-free (so "adjacent on the grid" is unambiguous)."""

    axes: dict[str, list[int]]

    def __post_init__(self) -> None:
        if not self.axes:
            raise ParamStabilityError("axes must not be empty")
        for name, values in self.axes.items():
            if len(set(values)) != len(values) or list(values) != sorted(values):
                raise ParamStabilityError(f"axis {name!r}: values must be strictly ascending")
        if self.size < MIN_GRID_SIZE:
            raise ParamStabilityError(
                f"grid size {self.size} < {MIN_GRID_SIZE} -- unreproducible configuration"
            )

    @property
    def size(self) -> int:
        result = 1
        for values in self.axes.values():
            result *= len(values)
        return result

    def points(self) -> list[Point]:
        return list(product(*self.axes.values()))

    def neighbors(self, point: Point) -> list[Point]:
        """Points that differ from `point` by exactly one step on exactly one axis."""
        names = list(self.axes.keys())
        found: list[Point] = []
        for axis_index, name in enumerate(names):
            values = self.axes[name]
            value_index = values.index(point[axis_index])
            for adjacent_index in (value_index - 1, value_index + 1):
                if 0 <= adjacent_index < len(values):
                    neighbor = list(point)
                    neighbor[axis_index] = values[adjacent_index]
                    found.append(tuple(neighbor))
        return found


@dataclass(frozen=True, slots=True)
class ParameterStabilityReport:
    best: Point
    neighbor_mean: Decimal
    neighbor_std: Decimal
    isolated: bool


def stability_score(
    grid: ParamGrid, metric_by_point: dict[Point, Decimal]
) -> ParameterStabilityReport:
    """Finds the point with the highest metric, then compares it against the mean of
    its immediate grid neighbors. `isolated=True` when `neighbor_mean / best_value`
    falls below `NEIGHBOR_RATIO_ISOLATION_THRESHOLD` -- a sharp, non-robust peak."""
    points = grid.points()
    missing = [point for point in points if point not in metric_by_point]
    if missing:
        raise ParamStabilityError(f"metric_by_point is missing points: {missing}")

    best = max(points, key=lambda point: metric_by_point[point])
    best_value = metric_by_point[best]
    neighbors = grid.neighbors(best)
    if not neighbors:
        raise ParamStabilityError("best point has no neighbors on the grid")

    neighbor_values = [metric_by_point[neighbor] for neighbor in neighbors]
    neighbor_mean = sum(neighbor_values, Decimal(0)) / len(neighbor_values)
    neighbor_std = _population_stdev(neighbor_values, neighbor_mean)
    isolated = best_value > 0 and (neighbor_mean / best_value) < NEIGHBOR_RATIO_ISOLATION_THRESHOLD
    return ParameterStabilityReport(
        best=best, neighbor_mean=neighbor_mean, neighbor_std=neighbor_std, isolated=isolated
    )


def _population_stdev(values: list[Decimal], mean: Decimal) -> Decimal:
    if len(values) == 1:
        return Decimal(0)
    variance = sum(((value - mean) ** 2 for value in values), Decimal(0)) / len(values)
    return variance.sqrt()
