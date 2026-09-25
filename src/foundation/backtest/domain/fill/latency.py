"""BT-4 — Fill latency model (pure domain).

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§2.5 BT-4, §3.4(`latency_ms`).

Adds `latency_ms` to the order submission time to compute the "actual
market arrival time", then selects the first bar that opens after that
arrival time as the fill target. The bar list and submission time are
injected by the caller (no clock access — pure).
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta


def _require_utc(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be a tz-aware UTC datetime: {value}")


def _reject_negative_latency(latency_ms: int) -> None:
    if latency_ms < 0:
        raise ValueError(f"latency_ms must not be negative: {latency_ms}")


def delayed_arrival_time(*, submitted_at: datetime, latency_ms: int) -> datetime:
    """The time the order actually reaches the market after latency delay."""

    _require_utc(submitted_at, "submitted_at")
    _reject_negative_latency(latency_ms)
    return submitted_at + timedelta(milliseconds=latency_ms)


def resolve_execution_bar_index(
    *, submitted_at: datetime, latency_ms: int, bar_open_times: Sequence[datetime]
) -> int:
    """Return the index of the first bar that opens after the delayed arrival time.

    No look-ahead — a bar opening at exactly the arrival time is also
    unavailable since its information cannot be known yet; wait for the
    next bar (strict `>` comparison, same principle as
    `is_look_ahead_safe` in `domain/rules.py`).
    """

    arrival = delayed_arrival_time(submitted_at=submitted_at, latency_ms=latency_ms)
    for index, open_time in enumerate(bar_open_times):
        _require_utc(open_time, f"bar_open_times[{index}]")
        if open_time > arrival:
            return index
    raise LookupError("No bar opens after the arrival time — outside data range")
