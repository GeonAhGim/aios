"""DC-1 — instruments-v2 negative-path latency budget + gate-red reproduction.

Split from test_instruments_v2.py (file-size ratchet, CLAUDE.md §6 #12).
Rejection paths (naive datetime, malformed ULID) must stay under 1 ms CPU;
the virtual-delay tests prove the budget assertion trips red.
"""

from datetime import datetime

import pytest
from pydantic import ValidationError

from tests.conftest import PerfBudget
from tests.foundation.unit.market_data.contracts.test_instruments_v2 import (
    _now,
    _sample_instrument,
)
from tests.support.perf_inject import virtual_delayed_sync

_NOW_PATH = "tests.foundation.unit.market_data.contracts.test_instruments_v2._now"


@pytest.mark.perf
def test_instrument_naive_created_at_rejected_within_budget(perf_budget: PerfBudget) -> None:
    """Negative test: naive datetime is rejected, and rejection stays under 1 ms CPU.
    best_of avoids Windows CPU-quantisation noise."""
    with pytest.raises(ValidationError):
        _sample_instrument(created_at=datetime(2026, 9, 3, 0, 0))
    sample = perf_budget.best_of(
        lambda: pytest.raises(
            ValidationError, lambda: _sample_instrument(created_at=datetime(2026, 9, 3, 0, 0))
        ),
        n=5,
    )
    assert sample.cpu_ms < 1.0, f"naive datetime rejection took {sample.cpu_ms:.3f}ms (budget 1ms)"


@pytest.mark.perf
def test_instrument_naive_created_at_virtual_delay_trips_budget(
    perf_budget: PerfBudget, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gate-red reproduction: 5 ms virtual CPU delay in `_now` trips the budget."""
    monkeypatch.setattr(_NOW_PATH, virtual_delayed_sync(_now, 0.005))
    with pytest.raises(AssertionError, match="budget 1ms"):
        test_instrument_naive_created_at_rejected_within_budget(perf_budget)


@pytest.mark.perf
def test_instrument_invalid_ulid_rejected_within_budget(perf_budget: PerfBudget) -> None:
    """Negative test: malformed ULID is rejected, and rejection stays under 1 ms CPU."""
    with pytest.raises(ValidationError):
        _sample_instrument(instrument_id="not-a-ulid")
    sample = perf_budget.best_of(
        lambda: pytest.raises(
            ValidationError, lambda: _sample_instrument(instrument_id="not-a-ulid")
        ),
        n=5,
    )
    assert sample.cpu_ms < 1.0, f"invalid ULID rejection took {sample.cpu_ms:.3f}ms (budget 1ms)"


@pytest.mark.perf
def test_instrument_invalid_ulid_virtual_delay_trips_budget(
    perf_budget: PerfBudget, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gate-red reproduction: 5 ms virtual CPU delay in `_now` trips the budget."""
    monkeypatch.setattr(_NOW_PATH, virtual_delayed_sync(_now, 0.005))
    with pytest.raises(AssertionError, match="budget 1ms"):
        test_instrument_invalid_ulid_rejected_within_budget(perf_budget)
