"""RD-20 — 파싱 실패 공시 미처리 큐 포트.

Spec: docs/specs/L4_research_data_and_market_ecosystem_v1.0.md §9 RD-20 DoD
"파싱 실패 공시는 조용히 버리지 않고 미처리 큐에 남는다".
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
        """실패 원인과 함께 원문(본문 텍스트 제외, 구조화 필드만)을 남긴다.
        호출자는 이 이후 예외를 던지지 않는다 — 큐에 남는 것 자체가
        "조용히 버리지 않음"의 증거다."""
        ...
