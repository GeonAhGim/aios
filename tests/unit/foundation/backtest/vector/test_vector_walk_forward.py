"""BT-16a walk_forward — `backtest/vector/walk_forward.py` 테스트.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §9.9

- walk_forward: (a) 창 규격 음수/0 거부(negative). (b) 빈 조합 거부(negative).
  (c) 데이터가 창 하나보다 짧으면 거부(negative). (d) 훈련 구간 성과가 더
  좋은 조합이 검증 구간에서 선택돼 채점됨(정확성).

Shared fixtures (_rising_columns, _buy_and_hold_signal, _config, _CASH) are
imported from test_vector_sweeps to avoid duplication.
"""

from __future__ import annotations

import pytest

from src.foundation.backtest.vector.walk_forward import (
    WalkForwardError,
    WalkForwardWindowSpec,
    run_walk_forward,
)
from src.foundation.market_data.contracts.v1 import Timeframe

# ── shared fixtures (read-only) ──────────────────────────────────────────────
from tests.unit.foundation.backtest.vector.test_vector_sweeps import (
    _CASH,
    _buy_and_hold_signal,
    _config,
    _no_trade_signal,
    _rising_columns,
)


def _rising_columns_n(n: int) -> object:
    """Alias for clarity — delegates to shared fixture."""
    return _rising_columns(n)


# ================= walk_forward.py =================


def test_window_spec_rejects_non_positive_bars() -> None:
    with pytest.raises(WalkForwardError):
        WalkForwardWindowSpec(train_bars=0, test_bars=5)
    with pytest.raises(WalkForwardError):
        WalkForwardWindowSpec(train_bars=5, test_bars=0)
    with pytest.raises(WalkForwardError):
        WalkForwardWindowSpec(train_bars=5, test_bars=5, step_bars=0)


def test_walk_forward_rejects_empty_combos() -> None:
    with pytest.raises(WalkForwardError):
        run_walk_forward(
            _rising_columns(40),
            {},
            _config(),
            WalkForwardWindowSpec(train_bars=10, test_bars=5),
            timeframe=Timeframe.M1,
            initial_cash=_CASH,
        )


def test_walk_forward_rejects_data_shorter_than_one_window() -> None:
    n = 10
    combos = {"buy_hold": _buy_and_hold_signal(n)}
    with pytest.raises(WalkForwardError):
        run_walk_forward(
            _rising_columns(n),
            combos,
            _config(),
            WalkForwardWindowSpec(train_bars=10, test_bars=5),
            timeframe=Timeframe.M1,
            initial_cash=_CASH,
        )


def test_walk_forward_selects_better_train_combo_for_every_window() -> None:
    n = 40
    cols = _rising_columns(n)
    combos = {"buy_hold": _buy_and_hold_signal(n), "no_trade": _no_trade_signal(n)}
    window = WalkForwardWindowSpec(train_bars=10, test_bars=5)  # step defaults to 5

    result = run_walk_forward(
        cols,
        combos,
        _config(),
        window,
        timeframe=Timeframe.M1,
        initial_cash=_CASH,
    )

    expected_count = (n - window.train_bars - window.test_bars) // window.effective_step_bars + 1
    assert len(result.windows) == expected_count

    first = result.windows[0]
    assert (first.train_start, first.train_end) == (0, 10)
    assert (first.test_start, first.test_end) == (10, 15)
    for w in result.windows:
        assert w.selected_combo == "buy_hold"
        assert w.train_result.final_equity > _CASH
