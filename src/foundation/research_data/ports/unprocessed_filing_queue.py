"""RD-20 — Unprocessed queue port for filings that fail to parse.

Spec: docs/specs/L4_research_data_and_market_ecosystem_v1.0.md §9 RD-20 DoD
"filings that fail to parse are not silently dropped; they are left in
the unprocessed queue".
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

import asyncpg

__all__ = ["UnprocessedFilingQueue"]


@runtime_checkable
class UnprocessedFilingQueue(Protocol):
    async def enqueue(
        self,
        conn: asyncpg.Connection,
        *,
        source_id: str,
        raw_payload: dict[str, object],
        reason: str,
    ) -> None:
        """Leaves the raw data (structured fields only, no body text) along with
        the failure reason. The caller does not throw an exception after this
        — being left in the queue is itself the proof of "not silently
        dropped"."""
        ...
