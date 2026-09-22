"""RD-9 -- `domain/as_of_binding.py` tests.

Spec: docs/specs/L4_research_data_and_market_ecosystem_v1.0.md §9 RD-9
("expose `research.*` queries to scripts with `as_of` auto-binding"), §4
RD-A1.

Full D2 negative/failure-injection/perf/gate-red battery is deferred to the
follow-up leaf recorded in task-2710's `decision` (this leaf's scope was
narrowed to core implementation + wiring). This file still covers the
mandatory floor: negative tests for every rejection path plus the core
auto-bind/pass-through behaviour.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.foundation.research_data.domain.as_of_binding import AsOfBindingError, bind_as_of

_BAR_TS = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)


def test_auto_binds_to_bar_ts_when_omitted() -> None:
    assert bind_as_of(_BAR_TS) == _BAR_TS


def test_accepts_requested_as_of_not_after_bar_ts() -> None:
    earlier = _BAR_TS - timedelta(hours=1)
    assert bind_as_of(_BAR_TS, requested_as_of=earlier) == earlier


def test_accepts_requested_as_of_equal_to_bar_ts() -> None:
    assert bind_as_of(_BAR_TS, requested_as_of=_BAR_TS) == _BAR_TS


def test_rejects_naive_bar_ts() -> None:
    with pytest.raises(ValueError, match="tz-aware"):
        bind_as_of(datetime(2026, 6, 15, 12, 0, 0))


def test_rejects_naive_requested_as_of() -> None:
    with pytest.raises(ValueError, match="tz-aware"):
        bind_as_of(_BAR_TS, requested_as_of=datetime(2026, 6, 15))


def test_rejects_future_requested_as_of() -> None:
    """RD-A1: a script requesting `as_of` later than the current bar is a
    look-ahead attempt -- rejected fail-closed, never clamped."""
    future = _BAR_TS + timedelta(seconds=1)
    with pytest.raises(AsOfBindingError, match="future reference rejected"):
        bind_as_of(_BAR_TS, requested_as_of=future)


def test_future_request_rejection_is_not_silently_clamped() -> None:
    """Failure-injection: a caller that tries to bypass rejection by
    catching and retrying with the same future value must keep failing --
    there is no clamped fallback value to fall through to."""
    future = _BAR_TS + timedelta(days=365)
    for _ in range(3):
        with pytest.raises(AsOfBindingError):
            bind_as_of(_BAR_TS, requested_as_of=future)
