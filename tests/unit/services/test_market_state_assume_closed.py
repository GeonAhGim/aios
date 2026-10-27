"""build_market_state `assume_closed` fast path vs. the default U10 filter.

The backtest replay skips the per-bar closed-bar re-scan only after verifying
bar ordering once; these tests pin that the flag never changes results for
ordered input and that the default path still drops in-progress bars.
"""

from __future__ import annotations

from datetime import timedelta

from src.services.execution_loop.market_state import build_market_state
from tests.unit.services.test_market_state_multi_tf import _BASE, _candles, _fsm


def test_assume_closed_matches_default_filter_for_ordered_closed_bars() -> None:
    fsm = _fsm("SMA_timeperiod3 >= 1")
    bars = _candles(6, timeframe="1m", step=timedelta(minutes=1), start=_BASE)
    as_of = bars[-1].close_time

    default = build_market_state(fsm, {"1m": bars}, as_of=as_of)
    fast = build_market_state(fsm, {"1m": bars}, as_of=as_of, assume_closed=True)

    assert fast.values == default.values
    assert fast.bar_close_time == default.bar_close_time


def test_default_path_still_excludes_in_progress_bar() -> None:
    # Negative: without assume_closed the unclosed trailing bar must be dropped (U10).
    fsm = _fsm("SMA_timeperiod3 >= 1")
    bars = _candles(6, timeframe="1m", step=timedelta(minutes=1), start=_BASE)
    as_of = bars[-2].close_time

    state = build_market_state(fsm, {"1m": bars}, as_of=as_of)

    assert state.bar_close_time["1m"] == bars[-2].close_time
