"""RD-20 — asyncpg implementation of `UnprocessedFilingQueue`.

Spec: docs/specs/L4_research_data_and_market_ecosystem_v1.0.md §9 RD-20 DoD
"filings that fail to parse are not silently dropped; they are left in
the unprocessed queue" — this table
(`research_opendart_unprocessed_filing`) is the proof of that.
"""
from __future__ import annotations

import json

import asyncpg

__all__ = ["PostgresUnprocessedFilingQueue"]


class PostgresUnprocessedFilingQueue:
    async def enqueue(
        self,
        conn: asyncpg.Connection,
        *,
        source_id: str,
        raw_payload: dict[str, object],
        reason: str,
    ) -> None:
        await conn.execute(
            "INSERT INTO research_opendart_unprocessed_filing "
            "(source_id, raw_payload, reason) VALUES ($1, $2, $3)",
            source_id,
            json.dumps(raw_payload),
            reason,
        )
