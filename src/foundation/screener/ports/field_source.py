"""UX-6 — universe index + field-value lookup port the execution engine depends on.

`domain/evaluate.py`/`application/run_screen.py` only know this Protocol (same
convention as `ports/repository.py`, 71 §4) — the real I/O
(`adapters/postgres_field_source.py`) reuses the LA-24 instrument index
(`ReferenceReadRepository.list_instruments`) and DC-13 hot candle storage.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from src.foundation.market_data.contracts.v1 import InstrumentRef, Venue


class ScreenerFieldSource(Protocol):
    async def universe_page(
        self, *, venues: frozenset[Venue], after: UUID | None, limit: int
    ) -> list[InstrumentRef]:
        """`(venue, canonical_symbol, instrument_id)` ascending keyset page — same
        index LA-24's HTTP read API paginates with. Empty `venues` -> `[]`
        (fail-closed, mirrors `ReferenceReadRepository.list_instruments`)."""
        ...

    async def read_fields(
        self,
        *,
        instrument_ids_by_venue: Mapping[Venue, Sequence[UUID]],
        field_names: frozenset[str],
        as_of: datetime,
    ) -> dict[UUID, dict[str, Decimal]]:
        """Latest field values at/before `as_of`, batched per venue (not one round
        trip per instrument). An `instrument_id` absent from the result has no
        data yet for at least one requested field — the caller excludes that row
        instead of filling 0/NaN (same invariant `HotPostgresStorage` documents)."""
        ...
