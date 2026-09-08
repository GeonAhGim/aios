"""L26 -- `adapters/list_bars.ListBars` + `ports/bar_source.PointInTimeBars` 단위테스트.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md#L26 DoD (b)(c)(d).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.data.models.market_data import Candle
from src.foundation.backtest.adapters.list_bars import BacktestLookaheadError, ListBars
from src.foundation.backtest.ports.bar_source import PointInTimeBars


def _candle(index: int) -> Candle:
    open_time = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=index)
    price = Decimal(index + 1)
    return Candle(
        symbol="BTCUSDT",
        exchange="bitget",
        timeframe="1m",
        open=price,
        high=price,
        low=price,
        close=price,
        volume=Decimal("1"),
        open_time=open_time,
        close_time=open_time + timedelta(minutes=1),
    )


def _bars(count: int) -> list[Candle]:
    return [_candle(i) for i in range(count)]


# -- DoD (b) 구조적 타이핑 ----------------------------------------------------


def test_point_in_time_bars_protocol_matches_full_fake() -> None:
    class FakeBars:
        def upto(self, bar_index: int) -> list[Candle]:
            return []

        def at(self, bar_index: int) -> Candle:
            return _candle(0)

        def __len__(self) -> int:
            return 0

    assert isinstance(FakeBars(), PointInTimeBars) is True


def test_point_in_time_bars_protocol_rejects_missing_at() -> None:
    class FakeBarsWithoutAt:
        def upto(self, bar_index: int) -> list[Candle]:
            return []

        def __len__(self) -> int:
            return 0

    assert isinstance(FakeBarsWithoutAt(), PointInTimeBars) is False


def test_list_bars_satisfies_point_in_time_bars_protocol() -> None:
    assert isinstance(ListBars(_bars(5)), PointInTimeBars) is True


# -- DoD (c) 미래 참조 거부 ----------------------------------------------------


def test_at_future_index_raises_lookahead_error() -> None:
    bars = ListBars(_bars(5))
    with pytest.raises(BacktestLookaheadError) as exc_info:
        bars.at(len(bars))
    assert exc_info.value.error_code == "BACKTEST_LOOKAHEAD_VIOLATION"


def test_upto_future_index_raises_lookahead_error() -> None:
    bars = ListBars(_bars(5))
    with pytest.raises(BacktestLookaheadError) as exc_info:
        bars.upto(len(bars))
    assert exc_info.value.error_code == "BACKTEST_LOOKAHEAD_VIOLATION"


def test_at_negative_index_raises_lookahead_error_not_python_wraparound() -> None:
    bars = ListBars(_bars(5))
    with pytest.raises(BacktestLookaheadError) as exc_info:
        bars.at(-1)
    assert exc_info.value.error_code == "BACKTEST_LOOKAHEAD_VIOLATION"


def test_upto_negative_index_raises_lookahead_error_not_python_wraparound() -> None:
    bars = ListBars(_bars(5))
    with pytest.raises(BacktestLookaheadError) as exc_info:
        bars.upto(-1)
    assert exc_info.value.error_code == "BACKTEST_LOOKAHEAD_VIOLATION"


# -- DoD (d) 경계 정확값 -------------------------------------------------------


def test_upto_zero_returns_single_bar() -> None:
    bars = ListBars(_bars(5))
    assert len(bars.upto(0)) == 1


def test_upto_two_returns_three_bars_with_same_last_object() -> None:
    fixture = _bars(5)
    bars = ListBars(fixture)
    result = bars.upto(2)
    assert len(result) == 3
    assert result[-1] is fixture[2]


def test_upto_last_index_returns_all_bars() -> None:
    bars = ListBars(_bars(5))
    assert len(bars.upto(4)) == 5


def test_at_last_index_returns_same_object() -> None:
    fixture = _bars(5)
    bars = ListBars(fixture)
    assert bars.at(4) == fixture[4]


def test_len_matches_fixture_size() -> None:
    assert len(ListBars(_bars(5))) == 5


def test_upto_result_mutation_does_not_affect_internal_state() -> None:
    bars = ListBars(_bars(5))
    result = bars.upto(4)
    result.append(_candle(99))
    assert len(bars) == 5
