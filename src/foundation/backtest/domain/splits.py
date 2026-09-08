"""L33 -- `backtest/domain/splits.py`: walk-forward split generation (anchored/rolling)
with purge/embargo gaps.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md #9 L33 (contract: #2 table
`splits.py` row). Pure domain module -- no I/O, no randomness, stdlib only.

`make_splits` reserves `min_train` bars once at the head of the timeline (plus the
purge+embargo gap), then divides the remaining bars into `n_splits` equal, back-to-back
test folds. Anchored mode keeps every split's train window starting at bar 0
(expanding); rolling mode keeps a fixed `min_train`-bar train window that slides
forward with the test folds. Either way `test.start - train.stop == purge + embargo`
exactly, by construction, for every split.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

__all__ = [
    "MinTrainUnsatisfiableError",
    "OosLeakageError",
    "Split",
    "SplitError",
    "assert_no_overlap",
    "make_splits",
]


class SplitError(ValueError):
    """Base class for `splits.py` fail-closed rejections."""


class MinTrainUnsatisfiableError(SplitError):
    """`min_train` cannot be honored for the requested `n_splits` -- reject outright
    instead of silently shrinking `n_splits` (task-2386 DoD (c))."""


class OosLeakageError(SplitError):
    """`VALIDATION_OOS_LEAKAGE` -- `assert_no_overlap` found overlapping train/test
    indices, or a gap smaller than the split's own `purge + embargo`."""


@dataclass(frozen=True, slots=True)
class Split:
    """`train`/`test` are half-open, step-1 bar-index ranges. `purge`/`embargo` are
    the bar counts that produced this split's gap, carried along so
    `assert_no_overlap` can check the gap without a separate config parameter."""

    train: range
    test: range
    purge: int
    embargo: int

    def __post_init__(self) -> None:
        if self.train.step != 1 or self.test.step != 1:
            raise SplitError("train/test ranges must have step 1")
        if self.purge < 0 or self.embargo < 0:
            raise SplitError("purge/embargo must be >= 0")


def make_splits(
    n_bars: int,
    n_splits: int,
    mode: Literal["anchored", "rolling"],
    purge: int,
    embargo: int,
    min_train: int,
) -> list[Split]:
    """Builds `n_splits` walk-forward splits over bar indices `[0, n_bars)`.

    Raises `MinTrainUnsatisfiableError` rather than returning fewer splits when the
    data can't support the requested `n_splits` at the requested `min_train` -- a
    silent reduction would hide a misconfiguration from the caller.
    """
    if n_bars < 1 or n_splits < 1 or min_train < 1:
        raise SplitError("n_bars/n_splits/min_train must be >= 1")
    if mode not in ("anchored", "rolling"):
        raise SplitError(f"unknown mode: {mode}")
    if n_bars // n_splits < min_train:
        raise MinTrainUnsatisfiableError(
            f"n_bars // n_splits ({n_bars // n_splits}) < min_train ({min_train}) "
            f"for n_splits={n_splits} -- reduce n_splits or min_train explicitly"
        )
    gap = purge + embargo
    available_for_test = n_bars - min_train - gap
    if available_for_test < n_splits:
        raise MinTrainUnsatisfiableError(
            f"only {available_for_test} bars left for {n_splits} test folds after "
            f"reserving min_train={min_train} and gap={gap}"
        )
    test_size, remainder = divmod(available_for_test, n_splits)

    splits: list[Split] = []
    for i in range(n_splits):
        test_start = min_train + gap + i * test_size
        this_test_size = test_size + (remainder if i == n_splits - 1 else 0)
        test_end = test_start + this_test_size
        train_end = test_start - gap
        train_start = 0 if mode == "anchored" else train_end - min_train
        splits.append(Split(
            train=range(train_start, train_end), test=range(test_start, test_end),
            purge=purge, embargo=embargo,
        ))
    return splits


def assert_no_overlap(splits: Sequence[Split]) -> None:
    """Rejects any split whose train/test index sets intersect, or whose gap is
    smaller than its own `purge + embargo` -- both are OOS leakage. Raises on the
    first violation found (deterministic: split order is preserved), never logs a
    warning and continues."""
    for index, split in enumerate(splits):
        if set(split.train) & set(split.test):
            raise OosLeakageError(f"split {index}: train/test indices overlap")
        gap = split.test.start - split.train.stop
        if gap < split.purge + split.embargo:
            raise OosLeakageError(
                f"split {index}: gap {gap} < purge+embargo {split.purge + split.embargo}"
            )
