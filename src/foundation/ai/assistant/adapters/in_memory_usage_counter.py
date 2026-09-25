"""U-3a -- in-memory implementation of `UsageCounterStore`.

Counts reset on process restart and are not shared across multiple
instances -- documented here as a known limitation (not a hidden
regression) for the interim before AI-5/6 (general provider cost
accounting) land. At single-process, PAPER-scale deployment this limitation
is not a practical risk (the same premise as the FROZEN_PAPER_ONLY
operating scope).
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import date
from uuid import UUID


class InMemoryUsageCounterStore:
    def __init__(self) -> None:
        self._counts: dict[tuple[UUID, date], int] = defaultdict(int)
        self._lock = asyncio.Lock()

    async def get_count(self, *, tenant_id: UUID, day: date) -> int:
        async with self._lock:
            return self._counts[(tenant_id, day)]

    async def increment(self, *, tenant_id: UUID, day: date) -> None:
        async with self._lock:
            self._counts[(tenant_id, day)] += 1


_SINGLETON = InMemoryUsageCounterStore()


def get_process_usage_counter_store() -> InMemoryUsageCounterStore:
    """Single process-wide instance (same convention as IND-1's
    `DEFAULT_REGISTRY`) -- wrapped as a FastAPI dependency so tests can
    override it."""
    return _SINGLETON
