"""LA-9 — 계보(lineage) 배치·품질이슈 기록 포트.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.2, §5, §9.2 LA-9.

domain/application은 이 Protocol만 알고, 실제 구현
(adapters/postgres_batch_repository.py, LA-13)은 모른다(71번 §4).
`IngestBatchResult`(LA-1 contracts)를 그대로 배치 기록 표현으로 재사용한다
— `md_ingest_batch`는 INSERT only(§5)이므로 `create` 이후 갱신 메서드는
없다.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable
from uuid import UUID

import asyncpg

from src.foundation.market_data.contracts.v1 import IngestBatchResult, QualityIssue


@runtime_checkable
class BatchRepository(Protocol):
    async def create(
        self, conn: asyncpg.Connection, batch: IngestBatchResult
    ) -> IngestBatchResult:
        """`md_ingest_batch` INSERT only(§5). 같은 `batch_id` 재삽입은
        어댑터가 예외를 던진다."""
        ...

    async def add_issues(
        self, conn: asyncpg.Connection, batch_id: UUID, issues: list[QualityIssue]
    ) -> None:
        """`md_quality_issue`에 배치 단위로 기록. `issues`가 비어 있으면
        아무것도 쓰지 않는다."""
        ...

    async def get(self, conn: asyncpg.Connection, batch_id: UUID) -> IngestBatchResult | None:
        """없으면 `None`."""
        ...
