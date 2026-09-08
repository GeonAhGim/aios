"""RD-20 — Point-in-time lookup keyed on `known_at` (pure function, no I/O).

Spec: docs/specs/L4_research_data_and_market_ecosystem_v1.0.md §9 RD-20.

The store (`adapters/opendart/postgres_filing_repository.py`) appends
correcting filings only as new rows, never via UPDATE — so there can be
multiple `CorporateAction`s with the same `(instrument_id, action_type,
ex_date)` but different `known_at`. This function takes that full history
and picks the single value "we would have known" as of `as_of`: for each
group, it uses the latest `known_at` that is `<= as_of`. Asking about a
time before the correction returns the pre-correction value; asking about
a time after returns the corrected value — this is possible because both
remain in the store (had it been an UPDATE, the pre-correction value would
have been lost forever).
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from uuid import UUID

from src.foundation.market_data.contracts.v1 import CorporateAction

__all__ = ["resolve_as_of"]


def resolve_as_of(actions: Sequence[CorporateAction], as_of: datetime) -> list[CorporateAction]:
    if as_of.tzinfo is None:
        raise ValueError("as_of는 tz-aware datetime만 받는다")

    by_key: dict[tuple[UUID, str, date], CorporateAction] = {}
    for action in actions:
        known_at = action.known_at
        if known_at is None or known_at > as_of:
            continue
        key = (action.instrument_id, action.action_type, action.ex_date)
        current = by_key.get(key)
        current_known_at = current.known_at if current is not None else None
        if current_known_at is None or known_at > current_known_at:
            by_key[key] = action

    return sorted(by_key.values(), key=lambda a: (str(a.instrument_id), a.ex_date))
