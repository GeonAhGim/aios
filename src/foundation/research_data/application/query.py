"""RD-7 -- application/query.py: `search` with `as_of` point-in-time filter.

Spec: docs/specs/L4_research_data_and_market_ecosystem_v1.0.md §2.1
application/query.py, §4 RD-A1 ("an item with `known_at > as_of` is never
returned by any read path"), §9 RD-7.

This module owns the application-layer query that the API router and
backtest hooks call. It is a thin wrapper around the pure FA-9 bitemporal
kernel: every `ResearchItem` is modelled as a `BitemporalRecord` whose
`valid_from = tx_from = known_at` (the research item has no separate
valid-time axis), then filtered through `core.bitemporal.as_of`.

The function signature mirrors the spec table row:
  search(instruments, kinds, span, as_of)
where `as_of` defaults to "now" (the current time from the caller's clock).

Domain rules (known_at comparison, revision chain) are delegated to
`domain/known_at` / `domain/revision` — this file does not reimplement
time-comparison logic (RD-2 DoD (d), RD-3 DoD (d)).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone

from src.core.bitemporal import BitemporalRecord
from src.core.bitemporal import as_of as _bitemporal_as_of
from src.foundation.research_data.contracts.v1 import ResearchItem

__all__ = ["QueryFilter", "search"]


class QueryFilter:
    """Filter criteria for `search`.

    `as_of` is the point-in-time coordinate. When omitted (None), the
    caller should pass `now` from their clock — this class does not
    provide a clock itself (I/O boundary: the caller controls time).
    """

    def __init__(
        self,
        instruments: Sequence[str] | None = None,
        kinds: Sequence[str] | None = None,
        span: Sequence[str] | None = None,
        as_of: datetime | None = None,
    ) -> None:
        self.instruments = tuple(instruments) if instruments else None
        self.kinds = tuple(kinds) if kinds else None
        self.span = tuple(span) if span else None
        self.as_of = as_of

    @property
    def as_of_now(self) -> datetime:
        """Return `as_of` if set, otherwise current UTC time."""
        if self.as_of is not None:
            return self.as_of
        return datetime.now(timezone.utc)


def _item_to_record(item: ResearchItem) -> BitemporalRecord[ResearchItem]:
    """Model a `ResearchItem` as a bitemporal record for FA-9 filtering.

    Research items have no valid-time distinct from when they became known,
    so both axes collapse onto `known_at`. The interval is `[known_at,
    infinity)` — once published, the item stays visible until a correction
    with a later `known_at` out-ranks it (handled by the caller via
    `domain/revision.link_revision_chain`).
    """
    return BitemporalRecord(
        value=item,
        valid_from=item.known_at,
        valid_to=None,
        tx_from=item.known_at,
        tx_to=None,
    )


def search(
    items: Sequence[ResearchItem],
    *,
    instruments: Sequence[str] | None = None,
    kinds: Sequence[str] | None = None,
    span: Sequence[datetime] | None = None,
    as_of: datetime | None = None,
) -> tuple[ResearchItem, ...]:
    """Query research items with an `as_of` point-in-time filter.

    Parameters
    ----------
    items:
        The full set of candidate items (from a repository or cache).
    instruments:
        Optional filter — only items whose `instruments` tuple contains
        at least one of these instrument IDs.
    kinds:
        Optional filter — only items whose `kind` is in this set.
    span:
        Optional filter — items whose `published_at` falls within this
        `(start, end)` range (both inclusive).
    as_of:
        Point-in-time coordinate. Items with `known_at > as_of` are
        excluded (RD-A1). When omitted, uses the current UTC time.

    Returns
    -------
    tuple[ResearchItem, ...]
        Items matching all filters, ordered by `known_at` ascending then
        `item_id` ascending.

    Raises
    ------
    ValueError:
        If `as_of` is a naive datetime (tzinfo is None).
    """
    # Resolve as_of to a concrete value
    probe = as_of if as_of is not None else datetime.now(timezone.utc)

    # Validate tz-awareness
    if probe.tzinfo is None:
        raise ValueError("as_of must be tz-aware (naive datetime rejected)")

    # Step 1: PIT filter via FA-9 bitemporal kernel
    records = [_item_to_record(item) for item in items]
    pit_visible = _bitemporal_as_of(records, valid_time=probe, tx_time=probe)
    pit_items = [record.value for record in pit_visible]

    # Step 2: instrument filter
    if instruments:
        instrument_set = set(instruments)
        pit_items = [
            item for item in pit_items if any(inst in item.instruments for inst in instrument_set)
        ]

    # Step 3: kind filter
    if kinds:
        kind_set = set(kinds)
        pit_items = [item for item in pit_items if item.kind in kind_set]

    # Step 4: span filter (published_at within [start, end])
    if span and len(span) == 2:
        span_start, span_end = span
        if span_start is not None:
            pit_items = [item for item in pit_items if item.published_at >= span_start]
        if span_end is not None:
            pit_items = [item for item in pit_items if item.published_at <= span_end]

    # Sort: known_at ascending, then item_id ascending for determinism
    pit_items.sort(key=lambda i: (i.known_at, i.item_id))

    return tuple(pit_items)
