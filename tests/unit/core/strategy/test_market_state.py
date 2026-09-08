"""L08 -- src/core/strategy/market_state.py DoD (task-2456)."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.core.strategy.market_state import MarketState, assert_no_future

_SRC = Path("src/core/strategy")


def test_market_state_fields_match_spec() -> None:
    # DoD (a): drop a field and this fails.
    assert set(MarketState.model_fields) == {
        "schema_version",
        "as_of",
        "values",
        "bar_close_time",
    }
    assert MarketState.model_fields["schema_version"].default == "ms-v1"


def test_assert_no_future_rejects_bar_close_time_one_second_ahead() -> None:
    as_of = datetime(2026, 1, 1, tzinfo=timezone.utc)
    state = MarketState(
        as_of=as_of,
        values={},
        bar_close_time={"1h": as_of + timedelta(seconds=1)},
    )
    with pytest.raises(Exception, match="INTEGRITY_FUTURE_DATA"):
        assert_no_future(state)


def test_assert_no_future_allows_bar_close_time_equal_to_as_of() -> None:
    as_of = datetime(2026, 1, 1, tzinfo=timezone.utc)
    state = MarketState(as_of=as_of, values={}, bar_close_time={"1h": as_of})
    assert_no_future(state)  # must not raise


@pytest.mark.parametrize("module", ["market_state.py", "state_memory.py"])
def test_no_indicator_key_regex_defined_in_module(module: str) -> None:
    # DoD (d): the key grammar has exactly one owner (indicator_key.py).
    source = (_SRC / module).read_text(encoding="utf-8")
    assert "import re" not in source
    assert "re.compile" not in source
    assert not re.search(r"_KEY_RE\s*=", source)


def test_market_state_rejects_malformed_indicator_key() -> None:
    as_of = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with pytest.raises(ValidationError):
        MarketState(as_of=as_of, values={"RSI@": Decimal("1")}, bar_close_time={})


def test_market_state_rejects_naive_as_of() -> None:
    with pytest.raises(ValidationError):
        MarketState(as_of=datetime(2026, 1, 1), values={}, bar_close_time={})


def test_market_state_rejects_naive_bar_close_time() -> None:
    as_of = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with pytest.raises(ValidationError):
        MarketState(as_of=as_of, values={}, bar_close_time={"1h": datetime(2026, 1, 1)})


def test_market_state_rejects_float_values() -> None:
    as_of = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with pytest.raises(ValidationError):
        MarketState(as_of=as_of, values={"RSI_timeperiod14": 55.0}, bar_close_time={})


def test_from_flat_builds_single_timeframe_state() -> None:
    as_of = datetime(2026, 1, 1, tzinfo=timezone.utc)
    state = MarketState.from_flat({"RSI_timeperiod14": 55.0}, as_of=as_of, tf="1h")
    assert state.values == {"RSI_timeperiod14": Decimal("55.0")}
    assert state.bar_close_time == {"1h": as_of}
