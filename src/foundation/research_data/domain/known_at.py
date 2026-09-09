"""RD-2 -- `known_at` point-in-time check (pure, delegates to FA-9).

Spec: docs/specs/L4_research_data_and_market_ecosystem_v1.0.md §2.1 RD-2,
§3 (contract), §4 RD-A1 ("an item with `known_at > as_of` is never returned
by any read path").

`known_at` is the same concept as FA-9's (`src/core/bitemporal.py`)
`tx_from` (the moment the system came to know that fact) -- this module does
not rewrite the time-comparison rules, it calls `BitemporalRecord`/`as_of`
directly (reimplementation is forbidden, RD-2 DoD (d)). `ResearchItem` has
no `valid_from`/`valid_to` (a research item is "a fact published at that
point in time", not a state with a validity interval), so the `valid` axis
is filled in with `known_at` and left open-ended -- effectively a
specialization that only exercises the `tx_time` half of FA-9's four query
kinds, but it shares the same comparison kernel.

domain/** may not import I/O (L0-2) -- this file contains only pure
functions.
"""
from __future__ import annotations

from datetime import datetime

from src.core.bitemporal import BitemporalRecord, as_of
from src.foundation.research_data.contracts.v1 import (
    ResearchDataErrorCode,
    ResearchItem,
)

__all__ = ["PointInTimeViolationError", "assert_point_in_time"]


class PointInTimeViolationError(ValueError):
    """`RD_POINT_IN_TIME_VIOLATION` (409) -- `item.known_at` is strictly
    after `as_of`, so this item could not have been known to exist at this
    query time (RD-A1). The caller should re-query with a different
    `as_of`, not retry. The HTTP mapping is the API layer's job -- this
    module only performs the check."""

    error_code = ResearchDataErrorCode.POINT_IN_TIME_VIOLATION

    def __init__(self, item: ResearchItem, as_of_time: datetime) -> None:
        self.item = item
        self.as_of_time = as_of_time
        super().__init__(
            f"{self.error_code.value}: item_id={item.item_id} "
            f"known_at={item.known_at} as_of={as_of_time}"
        )


def assert_point_in_time(item: ResearchItem, as_of_time: datetime) -> None:
    """Passes if `item.known_at <= as_of_time`, otherwise raises
    `PointInTimeViolationError`.

    The boundary is decided by the FA-9 `as_of` kernel: it builds a
    one-row `BitemporalRecord` that uses `known_at` as both `valid_from`
    and `tx_from` (both the same coordinate, so this effectively collapses
    to a single tx-axis comparison), and checks whether the
    `(as_of_time, as_of_time)` coordinate falls inside that half-open
    interval `[known_at, infinity)`. `known_at == as_of_time` is included
    (`>=`); `known_at` even a microsecond later is excluded (`>`) -- this
    inequality direction cannot be flipped by hand here (FA-9 decides it).
    """
    record: BitemporalRecord[ResearchItem] = BitemporalRecord(
        value=item,
        valid_from=item.known_at,
        valid_to=None,
        tx_from=item.known_at,
        tx_to=None,
    )
    matches = as_of([record], valid_time=as_of_time, tx_time=as_of_time)
    if not matches:
        raise PointInTimeViolationError(item, as_of_time)
