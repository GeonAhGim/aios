"""Unit tests for `backtest/domain/splits.py` -- task-2386 L33 DoD (a)-(d)."""
from typing import Literal, cast

import pytest

from src.foundation.backtest.domain.splits import (
    MinTrainUnsatisfiableError,
    OosLeakageError,
    Split,
    assert_no_overlap,
    make_splits,
)

GAP = 15  # purge=10 + embargo=5, DoD (a) fixture


def _valid_splits(mode: Literal["anchored", "rolling"]) -> list[Split]:
    return make_splits(n_bars=1000, n_splits=5, mode=mode, purge=10, embargo=5, min_train=200)


def test_rolling_splits_have_no_leakage_and_exact_gap() -> None:
    splits = _valid_splits("rolling")
    assert len(splits) == 5
    for split in splits:
        assert set(split.train) & set(split.test) == set()
        assert split.test.start - split.train.stop == GAP


def test_anchored_splits_have_no_leakage_and_exact_gap() -> None:
    splits = _valid_splits("anchored")
    assert len(splits) == 5
    for split in splits:
        assert set(split.train) & set(split.test) == set()
        assert split.test.start - split.train.stop == GAP


def test_anchored_train_start_is_always_zero() -> None:
    splits = _valid_splits("anchored")
    assert [split.train.start for split in splits] == [0, 0, 0, 0, 0]
    assert [split.train.stop for split in splits] == [200, 357, 514, 671, 828]
    assert [split.test.start for split in splits] == [215, 372, 529, 686, 843]
    assert [split.test.stop for split in splits] == [372, 529, 686, 843, 1000]


def test_rolling_train_start_strictly_increases() -> None:
    splits = _valid_splits("rolling")
    starts = [split.train.start for split in splits]
    assert starts == [0, 157, 314, 471, 628]
    assert all(later > earlier for earlier, later in zip(starts, starts[1:], strict=False))
    assert [split.train.stop for split in splits] == [200, 357, 514, 671, 828]


def test_assert_no_overlap_accepts_valid_splits() -> None:
    assert_no_overlap(_valid_splits("rolling"))  # must not raise


def test_assert_no_overlap_rejects_overlapping_indices() -> None:
    bad = Split(train=range(0, 200), test=range(190, 300), purge=10, embargo=5)
    with pytest.raises(OosLeakageError):
        assert_no_overlap([bad])


def test_assert_no_overlap_rejects_insufficient_gap() -> None:
    bad = Split(train=range(0, 200), test=range(205, 300), purge=10, embargo=5)
    with pytest.raises(OosLeakageError):
        assert_no_overlap([bad])


def test_min_train_violation_is_rejected_not_reduced() -> None:
    with pytest.raises(MinTrainUnsatisfiableError):
        make_splits(n_bars=300, n_splits=5, mode="rolling", purge=10, embargo=5, min_train=200)


def test_min_train_violation_rejected_even_with_zero_gap() -> None:
    with pytest.raises(MinTrainUnsatisfiableError):
        make_splits(n_bars=300, n_splits=5, mode="anchored", purge=0, embargo=0, min_train=200)


def test_unknown_mode_is_rejected() -> None:
    with pytest.raises(ValueError):
        make_splits(
            n_bars=1000,
            n_splits=5,
            mode=cast(Literal["anchored", "rolling"], "sideways"),
            purge=10,
            embargo=5,
            min_train=200,
        )
