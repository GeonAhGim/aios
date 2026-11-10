"""sweep_universe pauses the cyclic GC for the sweep and always restores the caller's state.

Root cause of the loaded-host perf flake: GC rescans of the growing result set made the
100-symbol sweep exceed 1.5x the single-symbol baseline (same defect class as sweep_grid).
"""

from __future__ import annotations

import gc

import pytest

from src.foundation.backtest.vector import universe as universe_module
from src.foundation.backtest.vector.universe import sweep_universe
from src.foundation.market_data.contracts.v1 import Timeframe
from tests.unit.foundation.backtest.vector.test_vector_universe import (
    _CASH,
    _columns,
    _config,
    _no_signal,
)


def _universe(n_symbols: int = 2, bars: int = 20) -> dict:
    return {f"S{i}": (_columns(bars), _no_signal(bars)) for i in range(n_symbols)}


def test_gc_is_disabled_during_the_sweep_and_restored_after() -> None:
    seen: list[bool] = []
    real = universe_module.run_vector_backtest

    def _spy(*args, **kwargs):
        seen.append(gc.isenabled())
        return real(*args, **kwargs)

    assert gc.isenabled()
    original = universe_module.run_vector_backtest
    universe_module.run_vector_backtest = _spy
    try:
        sweep_universe(_universe(), _config(), timeframe=Timeframe.M1, initial_cash=_CASH)
    finally:
        universe_module.run_vector_backtest = original
    assert seen == [False, False]  # 스윕 중에는 꺼져 있다
    assert gc.isenabled()  # 끝나면 되돌린다


def test_gc_restored_when_a_symbol_fails() -> None:
    bad = {"bad": (_columns(20), _no_signal(5))}  # 길이 불일치 -> 도메인 오류
    with pytest.raises(Exception):  # noqa: B017 -- length mismatch surfaces as a domain error
        sweep_universe(bad, _config(), timeframe=Timeframe.M1, initial_cash=_CASH)
    assert gc.isenabled()


def test_gc_stays_disabled_if_caller_disabled_it() -> None:
    gc.disable()
    try:
        sweep_universe(_universe(1), _config(), timeframe=Timeframe.M1, initial_cash=_CASH)
        assert not gc.isenabled()
    finally:
        gc.enable()
