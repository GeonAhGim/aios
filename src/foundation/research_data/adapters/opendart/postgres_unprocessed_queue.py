"""RD-20 — `UnprocessedFilingQueue`의 asyncpg 구현.

Spec: docs/specs/L4_research_data_and_market_ecosystem_v1.0.md §9 RD-20 DoD
"파싱 실패 공시는 조용히 버리지 않고 미처리 큐에 남는다" — 이 테이블
(`research_opendart_unprocessed_filing`)이 그 증거다.
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
