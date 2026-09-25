"""U-3a -- per-tenant daily request counter port.

Where "how many requests has this tenant already spent today" is read from
and incremented is an adapter's responsibility (fake vs. in-memory vs. a
future redis/DB) -- `domain/budget.py`'s pure judgment function consumes
whatever this returns.
"""

from __future__ import annotations

from datetime import date
from typing import Protocol
from uuid import UUID


class UsageCounterStore(Protocol):
    async def get_count(self, *, tenant_id: UUID, day: date) -> int:
        """How many requests `tenant_id` has already spent on `day` (not
        counting this request)."""
        ...

    async def increment(self, *, tenant_id: UUID, day: date) -> None:
        """Record this one request against the counter. Call only after the
        budget check has passed (a rejected request does not consume
        quota)."""
        ...
