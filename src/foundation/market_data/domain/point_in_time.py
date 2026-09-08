"""DC-21 -- reference-data known_at rules for instrument_attributes.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
Section 9.10 DC-21 (depends on DC-4 `instruments` table, FA-9 `core/bitemporal.py`).

This module owns only the reference-data-specific policy: how a new
correction row is stamped with `known_at`, and how a set of raw
`instrument_attributes` rows collapses into "the attribute values known as
of a given instant." The as-of comparison itself -- is a fact's window
open at a given instant -- is not reimplemented here. It is delegated to
`src.core.bitemporal.as_of` (FA-9), by modeling each row's window as
`valid_from = tx_from = known_at` and `valid_to = tx_to = None`
(open-ended -- a correction never closes a prior row's window, it simply
out-ranks it by carrying a later `known_at`). No I/O happens in this
module; reading/writing rows is the adapter's job.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from src.core.bitemporal import BitemporalRecord
from src.core.bitemporal import as_of as bitemporal_as_of

__all__ = ["ReferenceAttribute", "assign_known_at", "latest_attributes_as_of"]


@dataclass(frozen=True, slots=True)
class ReferenceAttribute:
    """One `instrument_attributes` row. Corrections are new rows, never UPDATEs."""

    instrument_id: str
    attr_key: str
    attr_value: str
    known_at: datetime
    recorded_by: str | None = None


def assign_known_at(*, now: datetime) -> datetime:
    """Stamp a new correction row with its `known_at` value.

    `now` is a wall-clock read the caller passes in (this module does no
    I/O of its own). The only rule enforced here is the same tz-aware
    requirement FA-9 enforces on `valid_time`/`tx_time` -- naive datetimes
    are rejected rather than silently treated as UTC.
    """
    if now.tzinfo is None:
        raise ValueError("known_at must be tz-aware (naive datetime rejected)")
    return now


def _as_bitemporal_record(
    attribute: ReferenceAttribute,
) -> BitemporalRecord[ReferenceAttribute]:
    return BitemporalRecord(
        value=attribute,
        valid_from=attribute.known_at,
        valid_to=None,
        tx_from=attribute.known_at,
        tx_to=None,
    )


def latest_attributes_as_of(
    attributes: Sequence[ReferenceAttribute], *, as_of: datetime
) -> dict[str, ReferenceAttribute]:
    """Assemble the `known_at <= as_of` query filter, one row per `attr_key`.

    The instant comparison is FA-9's `core.bitemporal.as_of` query kernel,
    called with `valid_time = tx_time = as_of` -- reference-data
    corrections have no valid-time distinct from the instant they became
    known, so both bitemporal coordinates collapse onto `as_of`. This
    function adds only the reference-data policy on top of that filter:
    among the rows the kernel reports visible at `as_of`, keep the one with
    the greatest `known_at` per `attr_key` (the latest known correction
    wins; a later correction with a `known_at` still in the future of
    `as_of` must not leak through).
    """
    records = [_as_bitemporal_record(attribute) for attribute in attributes]
    visible = bitemporal_as_of(records, valid_time=as_of, tx_time=as_of)

    latest: dict[str, ReferenceAttribute] = {}
    for record in visible:
        attribute = record.value
        current = latest.get(attribute.attr_key)
        if current is None or attribute.known_at > current.known_at:
            latest[attribute.attr_key] = attribute
    return latest
