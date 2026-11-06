"""sweep_grid pauses the cyclic GC for the sweep and always restores the caller's state."""

from __future__ import annotations

import gc

import pytest

from src.foundation.backtest.vector.grid import sweep_grid
from src.foundation.market_data.contracts.v1 import Timeframe
from tests.unit.foundation.backtest.vector.test_grid_sweep_perf import (
    _CASH,
    _buy_and_hold_signal,
    _config,
    _rising_columns,
)


def test_gc_state_preserved_after_sweep() -> None:
    cols = _rising_columns(20)
    assert gc.isenabled()
    sweep_grid(
        cols, {"a": _buy_and_hold_signal(20)}, _config(), timeframe=Timeframe.M1, initial_cash=_CASH
    )
    assert gc.isenabled()


def test_gc_restored_when_combo_fails() -> None:
    cols = _rising_columns(20)
    with pytest.raises(Exception):  # noqa: B017 -- length mismatch surfaces as a domain error
        sweep_grid(
            cols,
            {"bad": _buy_and_hold_signal(5)},
            _config(),
            timeframe=Timeframe.M1,
            initial_cash=_CASH,
        )
    assert gc.isenabled()


def test_gc_stays_disabled_if_caller_disabled_it() -> None:
    cols = _rising_columns(20)
    gc.disable()
    try:
        sweep_grid(
            cols,
            {"a": _buy_and_hold_signal(20)},
            _config(),
            timeframe=Timeframe.M1,
            initial_cash=_CASH,
        )
        assert not gc.isenabled()
    finally:
        gc.enable()
