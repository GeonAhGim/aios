"""L08 -- src/core/strategy/state_memory.py DoD (task-2456)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from src.core.strategy.market_state import MarketState
from src.core.strategy.state_memory import StrategyStateMemory, advance

_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
_T1 = _T0 + timedelta(hours=1)
_T2 = _T1 + timedelta(hours=1)


def test_strategy_state_memory_fields_match_spec() -> None:
    # DoD (a): drop a field and this fails.
    assert set(StrategyStateMemory.model_fields) == {
        "schema_version",
        "execution_id",
        "state_version",
        "last_bar_time",
        "prev_values",
    }
    assert StrategyStateMemory.model_fields["schema_version"].default == "ssm-v1"


def test_advance_does_not_mutate_input_and_bumps_state_version() -> None:
    original = StrategyStateMemory(
        execution_id=1,
        state_version=0,
        last_bar_time={"1h": _T0},
        prev_values={"RSI_timeperiod14@1h": Decimal("50")},
    )
    market_state = MarketState(
        as_of=_T1,
        values={"RSI_timeperiod14@1h": Decimal("55")},
        bar_close_time={"1h": _T1},
    )

    result = advance(original, market_state, bar_times={"1h": _T0})

    # Original is untouched -- all three fields checked (DoD b).
    assert original.prev_values == {"RSI_timeperiod14@1h": Decimal("50")}
    assert original.last_bar_time == {"1h": _T0}
    assert original.state_version == 0

    assert result.state_version == original.state_version + 1


def test_advance_carries_prev_values_when_bar_times_matches_last_bar_time() -> None:
    original = StrategyStateMemory(
        execution_id=1,
        state_version=0,
        last_bar_time={"1h": _T0},
        prev_values={"RSI_timeperiod14@1h": Decimal("50")},
    )
    market_state = MarketState(
        as_of=_T1,
        values={"RSI_timeperiod14@1h": Decimal("55")},
        bar_close_time={"1h": _T1},
    )

    result = advance(original, market_state, bar_times={"1h": _T0})

    assert result.prev_values == {"RSI_timeperiod14@1h": Decimal("55")}
    assert result.last_bar_time == {"1h": _T1}


def test_advance_drops_prev_values_for_a_skipped_timeframe() -> None:
    # memory's recorded last bar for "1h" is _T0, but the caller asserts the
    # immediately preceding bar was _T1 (one bar later than what's on record) --
    # a bar was skipped between the recorded state and this tick.
    original = StrategyStateMemory(
        execution_id=1,
        state_version=0,
        last_bar_time={"1h": _T0},
        prev_values={"RSI_timeperiod14@1h": Decimal("50")},
    )
    market_state = MarketState(
        as_of=_T2,
        values={"RSI_timeperiod14@1h": Decimal("60")},
        bar_close_time={"1h": _T2},
    )

    result = advance(original, market_state, bar_times={"1h": _T1})

    assert "RSI_timeperiod14@1h" not in result.prev_values
    # last_bar_time still records the newest observed bar for next tick's check.
    assert result.last_bar_time == {"1h": _T2}


def test_state_memory_rejects_naive_last_bar_time() -> None:
    with pytest.raises(ValidationError):
        StrategyStateMemory(
            execution_id=1,
            state_version=0,
            last_bar_time={"1h": datetime(2026, 1, 1)},
            prev_values={},
        )


def test_state_memory_rejects_float_prev_values() -> None:
    with pytest.raises(ValidationError):
        StrategyStateMemory(
            execution_id=1,
            state_version=0,
            last_bar_time={},
            prev_values={"RSI_timeperiod14@1h": 50.0},
        )
