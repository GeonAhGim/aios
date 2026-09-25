"""DC-21 adversarial -- `domain/point_in_time.py` known_at leakage + delegation proof.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
Section 9.10 DC-21 DoD: "a correction is a new row, a query returns only
rows with known_at <= as_of" (adversarial), plus the FA-9 `core/bitemporal`
delegation proof ("removing the as_of/valid_time comparison logic from
point_in_time.py must break a test").

Pure domain-level tests -- no database needed, `point_in_time.py` does no
I/O.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import src.core.bitemporal as bitemporal
from src.foundation.market_data.domain.point_in_time import (
    ReferenceAttribute,
    assign_known_at,
    latest_attributes_as_of,
)

_INSTRUMENT_ID = "TEST-INSTRUMENT-X"
_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
_T1 = _T0 + timedelta(days=1)


def test_correction_at_t1_does_not_leak_before_t1() -> None:
    """DoD (a): lot_size=10 at T0, corrected to 100 at T1; as_of=T0+1s must
    still see 10 -- a query strictly between T0 and T1 must not see the
    not-yet-known T1 correction."""
    original = ReferenceAttribute(
        instrument_id=_INSTRUMENT_ID, attr_key="lot_size", attr_value="10", known_at=_T0
    )
    correction = ReferenceAttribute(
        instrument_id=_INSTRUMENT_ID, attr_key="lot_size", attr_value="100", known_at=_T1
    )

    result = latest_attributes_as_of([original, correction], as_of=_T0 + timedelta(seconds=1))

    assert result["lot_size"].attr_value == "10", (
        "as_of before the T1 correction leaked the T1 value -- "
        f"got {result['lot_size'].attr_value!r}"
    )


def test_correction_visible_once_as_of_reaches_its_known_at() -> None:
    original = ReferenceAttribute(
        instrument_id=_INSTRUMENT_ID, attr_key="lot_size", attr_value="10", known_at=_T0
    )
    correction = ReferenceAttribute(
        instrument_id=_INSTRUMENT_ID, attr_key="lot_size", attr_value="100", known_at=_T1
    )

    result = latest_attributes_as_of([original, correction], as_of=_T1)

    assert result["lot_size"].attr_value == "100"


def test_as_of_before_any_known_at_returns_nothing() -> None:
    original = ReferenceAttribute(
        instrument_id=_INSTRUMENT_ID, attr_key="lot_size", attr_value="10", known_at=_T0
    )

    result = latest_attributes_as_of([original], as_of=_T0 - timedelta(seconds=1))

    assert "lot_size" not in result


def test_distinct_attr_keys_are_tracked_independently() -> None:
    lot_size = ReferenceAttribute(
        instrument_id=_INSTRUMENT_ID, attr_key="lot_size", attr_value="10", known_at=_T0
    )
    tick_size = ReferenceAttribute(
        instrument_id=_INSTRUMENT_ID, attr_key="tick_size", attr_value="0.01", known_at=_T0
    )

    result = latest_attributes_as_of([lot_size, tick_size], as_of=_T1)

    assert result["lot_size"].attr_value == "10"
    assert result["tick_size"].attr_value == "0.01"


def test_assign_known_at_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError, match="tz-aware"):
        assign_known_at(now=datetime(2026, 1, 1))


def test_assign_known_at_returns_the_given_tz_aware_instant() -> None:
    assert assign_known_at(now=_T0) == _T0


def test_latest_attributes_as_of_rejects_naive_as_of() -> None:
    """Negative: a tz-naive `as_of` must be rejected, not silently treated as
    UTC -- the naive-datetime guard is FA-9's (`core.bitemporal._require_tz_aware`),
    reached through delegation, so this also proves the delegation is live on
    the failure path, not just the happy path covered by the spy test below."""
    attribute = ReferenceAttribute(
        instrument_id=_INSTRUMENT_ID, attr_key="lot_size", attr_value="10", known_at=_T0
    )

    with pytest.raises(ValueError, match="naive"):
        latest_attributes_as_of([attribute], as_of=datetime(2026, 1, 2))


def test_latest_attributes_as_of_rejects_naive_known_at() -> None:
    """Negative: a `ReferenceAttribute` carrying a tz-naive `known_at` must be
    rejected when assembled into a bitemporal record, not silently coerced --
    a naive `known_at` could otherwise leak through comparisons against the
    tz-aware `as_of` and produce an undefined ordering."""
    attribute = ReferenceAttribute(
        instrument_id=_INSTRUMENT_ID,
        attr_key="lot_size",
        attr_value="10",
        known_at=datetime(2026, 1, 1),
    )

    with pytest.raises(ValueError, match="naive"):
        latest_attributes_as_of([attribute], as_of=_T1)


def test_latest_attributes_as_of_propagates_bitemporal_kernel_failure(monkeypatch) -> None:
    """Failure injection: if the FA-9 kernel this module delegates to raises
    (e.g. a regression reintroduces an overlap check or otherwise starts
    raising), `latest_attributes_as_of` must propagate the exception rather
    than swallow it and return a partial/empty result -- fail-closed per
    CLAUDE.md `#3` default posture."""

    def raising_as_of(records, *, valid_time, tx_time):
        raise RuntimeError("core.bitemporal.as_of kernel failure (injected)")

    monkeypatch.setattr(
        "src.foundation.market_data.domain.point_in_time.bitemporal_as_of", raising_as_of
    )

    attribute = ReferenceAttribute(
        instrument_id=_INSTRUMENT_ID, attr_key="lot_size", attr_value="10", known_at=_T0
    )

    with pytest.raises(RuntimeError, match="injected"):
        latest_attributes_as_of([attribute], as_of=_T1)


def test_latest_attributes_as_of_delegates_to_core_bitemporal(monkeypatch) -> None:
    """DoD (c): the as_of instant comparison must be FA-9's `core.bitemporal.as_of`
    kernel, not a reimplementation. Proven by spying on the real delegate --
    if `point_in_time.py` stopped calling it (e.g. reimplemented the
    `known_at <= as_of` comparison locally), this call-count assertion would
    fail even though the surrounding value-leakage tests above might still
    pass by coincidence."""
    calls: list[dict[str, object]] = []
    real_as_of = bitemporal.as_of

    def spy_as_of(records, *, valid_time, tx_time):
        calls.append({"valid_time": valid_time, "tx_time": tx_time})
        return real_as_of(records, valid_time=valid_time, tx_time=tx_time)

    monkeypatch.setattr(
        "src.foundation.market_data.domain.point_in_time.bitemporal_as_of", spy_as_of
    )

    attribute = ReferenceAttribute(
        instrument_id=_INSTRUMENT_ID, attr_key="lot_size", attr_value="10", known_at=_T0
    )
    latest_attributes_as_of([attribute], as_of=_T1)

    assert len(calls) == 1, "latest_attributes_as_of did not delegate to core.bitemporal.as_of"
    assert calls[0] == {"valid_time": _T1, "tx_time": _T1}
