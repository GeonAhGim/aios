"""LA-9 — 계보(lineage) 배치·품질이슈 기록 포트.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.2, §5, §9.2 LA-9,
LA-16a.

domain/application은 이 Protocol만 알고, 실제 구현
(adapters/postgres_batch_repository.py, LA-13/LA-16a)은 모른다(71번 §4).
`IngestBatchResult`(LA-1 contracts)를 그대로 배치 기록 표현으로 재사용한다
— `md_ingest_batch`는 INSERT only(§5)이므로 `create` 이후 갱신 메서드는
없다. `create_tick_batch`/`get_tick_batch`는 LA-16a가 추가한 틱 배치용
메서드다(`TickIngestBatchResult`, `md_ingest_batch_tick` — `timeframe`
없는 별도 테이블, task-656 note).
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable
from uuid import UUID

import asyncpg

from src.foundation.market_data.contracts.v1 import (
    IngestBatchResult,
    QualityIssue,
    TickIngestBatchResult,
)


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

    async def get(
        self, conn: asyncpg.Connection, batch_id: UUID, tenant_id: UUID | None
    ) -> IngestBatchResult | None:
        """`tenant_id`가 배치 소유자와 다르면(비교 대상 없는 플랫폼 공용
        배치 포함) 존재 자체를 숨기고 `None`을 반환한다(§8.3 LA-21
        "404 동형") — 없어서 `None`인지 남의 tenant 것이라 `None`인지
        호출부가 구분할 수 없어야 한다."""
        ...

    async def create_tick_batch(
        self, conn: asyncpg.Connection, batch: TickIngestBatchResult
    ) -> TickIngestBatchResult:
        """`md_ingest_batch_tick` INSERT only. 같은 `batch_id` 재삽입은
        어댑터가 예외를 던진다."""
        ...

    async def get_tick_batch(
        self, conn: asyncpg.Connection, batch_id: UUID, tenant_id: UUID | None
    ) -> TickIngestBatchResult | None:
        """`get()`과 동일한 tenant 격리 규칙(§8.3 LA-21 "404 동형")."""
        ...
