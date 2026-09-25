"""FA-9 — Bitemporal (valid_time · transaction_time) pure query rules.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-9
(§2.3 bitemporal ledger, §3 contract "bitemporal", §4 FA-A2 UPDATE/DELETE prohibition).

Defines only pure interval arithmetic matching Postgres `TSTZRANGE`
(`valid_from`, `valid_to`, `tx_from`, `tx_to`) — actual DDL, EXCLUDE
constraints, and UPDATE-prohibit triggers belong to FA-10; this module
performs zero I/O.

Boundary contract: both intervals are half-open `[from, to)` — `from` is
inclusive, `to` is exclusive (same canonical form as Postgres `TSTZRANGE`).
"Current" rows use `to=None` (infinity, corresponding to
`tx_to = 'infinity'`). tz-naive datetimes are rejected at the input stage
(LB-1/EO-01 precedent —
`src/foundation/execution_ownership/domain/rules.py:is_lease_available`).

`as_of(valid_time, tx_time)` is the sole query kernel. The spec's "four
query types" are the 2×2 combinations of passing `valid_time`/`tx_time`
as explicit values or `now` into this kernel (the four functions below
are named aliases of those combinations):
  1. `current`               — (valid=now,  tx=now)  what is true now
  2. `as_of_valid_time`      — (valid=given, tx=now) past point in time under latest knowledge
  3. `as_of_transaction_time`— (valid=now,   tx=given) "now" under past knowledge
  4. `as_of_bitemporal`      — (valid=given, tx=given) full retroactive (rollback) query
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Generic, TypeVar

T = TypeVar("T")

FA_BITEMPORAL_OVERLAP = "FA_BITEMPORAL_OVERLAP"


class BitemporalOverlapError(ValueError):
    """FA_BITEMPORAL_OVERLAP(409) — Two intervals for the same key
    overlap on both valid and tx axes.

    HTTP mapping (§3 error taxonomy) is the API layer's concern —
    this module only decides.
    """

    error_code = FA_BITEMPORAL_OVERLAP

    def __init__(self, first: BitemporalRecord[Any], second: BitemporalRecord[Any]):
        self.first = first
        self.second = second
        super().__init__(
            f"{FA_BITEMPORAL_OVERLAP}: valid/tx intervals overlap — "
            f"first=[{first.valid_from},{first.valid_to})x[{first.tx_from},{first.tx_to}) "
            f"second=[{second.valid_from},{second.valid_to})x[{second.tx_from},{second.tx_to})"
        )


def _require_tz_aware(value: datetime, *, name: str) -> None:
    """Ensure `value` is timezone-aware."""
    if value.tzinfo is None:
        raise ValueError(f"{name}: naive datetime is not allowed — use tz-aware UTC only")


def _require_valid_bound(from_: datetime, to: datetime | None, *, label: str) -> None:
    """Ensure `from_` and `to` are tz-aware and `to > from_`.

    Half-open intervals must be non-empty.
    """
    _require_tz_aware(from_, name=f"{label}_from")
    if to is not None:
        _require_tz_aware(to, name=f"{label}_to")
        if to <= from_:
            raise ValueError(
                f"{label}_to must be after {label}_from "
                "(half-open interval must be non-empty)"
            )


@dataclass(frozen=True, slots=True)
class BitemporalRecord(Generic[T]):
    """Pure value object corresponding to one row in a state-change table.

    `value` is valid only at coordinates where
    `valid_from <= valid_time < valid_to` and `tx_from <= tx_time < tx_to`.
    `valid_to`/`tx_to` as `None` means infinity (respectively "end date
    not yet determined" and "current row not yet corrected").
    """

    value: T
    valid_from: datetime
    valid_to: datetime | None
    tx_from: datetime
    tx_to: datetime | None

    def __post_init__(self) -> None:
        _require_valid_bound(self.valid_from, self.valid_to, label="valid")
        _require_valid_bound(self.tx_from, self.tx_to, label="tx")

    def _contains_valid(self, instant: datetime) -> bool:
        """Check whether `instant` falls within the valid interval."""
        return self.valid_from <= instant and (self.valid_to is None or instant < self.valid_to)

    def _contains_tx(self, instant: datetime) -> bool:
        """Check whether `instant` falls within the transaction interval."""
        return self.tx_from <= instant and (self.tx_to is None or instant < self.tx_to)

    def contains(self, *, valid_time: datetime, tx_time: datetime) -> bool:
        """Does this record cover the given (valid_time, tx_time) coordinates?"""
        return self._contains_valid(valid_time) and self._contains_tx(tx_time)

    def overlaps(self, other: BitemporalRecord[Any]) -> bool:
        """Return True when both valid and tx intervals overlap.

        Used for FA_BITEMPORAL_OVERLAP decision.
        """
        return _ranges_overlap(
            self.valid_from, self.valid_to, other.valid_from, other.valid_to
        ) and _ranges_overlap(self.tx_from, self.tx_to, other.tx_from, other.tx_to)


def _ranges_overlap(
    a_from: datetime, a_to: datetime | None,
    b_from: datetime, b_to: datetime | None,
) -> bool:
    """Check whether two half-open intervals `[from, to)` overlap."""
    a_ends_before_b_starts = a_to is not None and a_to <= b_from
    b_ends_before_a_starts = b_to is not None and b_to <= a_from
    return not (a_ends_before_b_starts or b_ends_before_a_starts)


def check_no_overlap(records: Sequence[BitemporalRecord[T]]) -> None:
    """Reject if any pair of records in the same key group has overlapping valid·tx intervals.

    FA-A2 (state-change table UPDATE/DELETE prohibition — corrections use a new row
    with `tx_to` to close the old one) is pre-validated at the pure function level
    before storage. Actual DB constraints (`EXCLUDE USING gist`) belong to FA-10;
    this module provides only the application-level fail-closed defense line.
    Callers must pass records already grouped by the same logical entity
    (e.g. the same `position_id`) — this function knows nothing about group boundaries.
    """
    for i, first in enumerate(records):
        for second in records[i + 1 :]:
            if first.overlaps(second):
                raise BitemporalOverlapError(first, second)


def as_of(
    records: Sequence[BitemporalRecord[T]], *, valid_time: datetime, tx_time: datetime
) -> list[BitemporalRecord[T]]:
    """Query kernel: all records covering `(valid_time, tx_time)` coordinates.

    For a single entity loaded without overlaps the result is 0 or 1 —
    overlap prevention is guaranteed by `check_no_overlap` at load time.
    This function makes no such assumption and simply filters.
    """
    _require_tz_aware(valid_time, name="valid_time")
    _require_tz_aware(tx_time, name="tx_time")
    return [r for r in records if r.contains(valid_time=valid_time, tx_time=tx_time)]


def current(records: Sequence[BitemporalRecord[T]], *, now: datetime) -> list[BitemporalRecord[T]]:
    """Query 1/4: what is true now (`now`) and known now (`now`)."""
    return as_of(records, valid_time=now, tx_time=now)


def as_of_valid_time(
    records: Sequence[BitemporalRecord[T]], *, valid_time: datetime, now: datetime
) -> list[BitemporalRecord[T]]:
    """Query 2/4: what was true at `valid_time`, under the latest knowledge at `now`.

    Example: "what was the position value on the 15th of last month, as we know it today."
    """
    return as_of(records, valid_time=valid_time, tx_time=now)


def as_of_transaction_time(
    records: Sequence[BitemporalRecord[T]], *, tx_time: datetime, now: datetime
) -> list[BitemporalRecord[T]]:
    """Query 3/4: what the system knew "now" (`now`) about, at past system time (`tx_time`).

    Example: "what position value did the system know for today, as of yesterday's
    close (`tx_time`)" — a rollback query reproducing a pre-correction value.
    """
    return as_of(records, valid_time=now, tx_time=tx_time)


def as_of_bitemporal(
    records: Sequence[BitemporalRecord[T]], *, valid_time: datetime, tx_time: datetime
) -> list[BitemporalRecord[T]]:
    """Query 4/4: full retroactive — what the system believed at system time
    `tx_time` to be true at `valid_time`. Core query for reconstructing
    both IBOR (today's known truth) and ABOR (the ledger as it was then)
    from the same data store (§1 requirement)."""
    return as_of(records, valid_time=valid_time, tx_time=tx_time)
