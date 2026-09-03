"""LA-13 — `BatchRepository`(ports/batch_repository.py)의 asyncpg 구현.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.2, §5, §9.2 LA-13.

`create()`는 `IngestBatchResult`(LA-9 포트 시그니처, task-615 note로 확장된
필드 포함)를 `md_ingest_batch`에 그대로 INSERT only로 옮긴다(같은 `batch_id`
재삽입은 PK 위반 → `DuplicateBatchError`). `md_ingest_batch.audit_event_id`는
NOT NULL(§4.1 fail-closed — 모든 배치 판정은 감사 이벤트를 낸다)이라
`batch.audit_event_id`가 `None`이면 DB에 보내기 전에 먼저 거부한다(더 분명한
오류를 fail-fast로).

`get()`은 문제가 하나 있다: `md_ingest_batch`는 `record_count`(총계) 하나만
저장하고, 원본 `QualityVerdict.accepted/quarantined/rejected`의 3분류는
저장하지 않는다(LA-11이 실제로 만든 컬럼이 그렇다 — 이 리프는 새 마이그레이션을
만들 수 없다, task note). 그래서 `accepted`는 `md_candle`에 실제로 그
`batch_id`로 저장된 행 수, `quarantined`는 `md_quarantine_candle`의 행 수로
다시 계산하고, `rejected`는 `record_count - accepted - quarantined`로
역산한다(둘 다 append-only라 값이 바뀌지 않는다는 전제 — `create()`가
`record_count`를 정확히 썼다면 항상 참이다). `stored_range`도 마찬가지로
컬럼이 없어 `md_candle`의 `MIN/MAX(open_time)`으로 재계산한다.
"""
from __future__ import annotations

import json
from typing import cast
from uuid import UUID

import asyncpg
from pydantic import AwareDatetime

from src.foundation.market_data.contracts.v1 import (
    IngestBatchResult,
    QualityIssue,
    QualityIssueType,
    QualityVerdict,
    Severity,
    Timeframe,
    Venue,
    Verdict,
)

__all__ = ["DuplicateBatchError", "PostgresBatchRepository"]


class DuplicateBatchError(Exception):
    """`create()`가 이미 존재하는 `batch_id`로 다시 불림 — `md_ingest_batch`는
    INSERT only라 갱신이 아니라 명시적 거부다."""


async def _reconstruct_verdict(conn: asyncpg.Connection, row: asyncpg.Record) -> QualityVerdict:
    accepted = await conn.fetchval(
        "SELECT COUNT(*) FROM md_candle WHERE batch_id = $1", row["id"]
    )
    quarantined = await conn.fetchval(
        "SELECT COUNT(*) FROM md_quarantine_candle WHERE batch_id = $1", row["id"]
    )
    rejected = max(row["record_count"] - accepted - quarantined, 0)

    issue_rows = await conn.fetch(
        "SELECT type, severity, open_time, detail FROM md_quality_issue "
        "WHERE batch_id = $1 ORDER BY id ASC",
        row["id"],
    )
    issues = [
        QualityIssue(
            type=QualityIssueType(issue_row["type"]),
            severity=Severity(issue_row["severity"]),
            open_time=issue_row["open_time"],
            detail=json.loads(issue_row["detail"]),
        )
        for issue_row in issue_rows
    ]

    return QualityVerdict(
        verdict=Verdict(row["verdict"]),
        accepted=accepted,
        quarantined=quarantined,
        rejected=rejected,
        issues=issues,
    )


async def _stored_range(
    conn: asyncpg.Connection, batch_id: UUID
) -> tuple[AwareDatetime, AwareDatetime] | None:
    row = await conn.fetchrow(
        "SELECT MIN(open_time) AS range_start, MAX(open_time) AS range_end "
        "FROM md_candle WHERE batch_id = $1",
        batch_id,
    )
    if row is None or row["range_start"] is None:
        return None
    return cast(
        "tuple[AwareDatetime, AwareDatetime]", (row["range_start"], row["range_end"])
    )


class PostgresBatchRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def create(
        self, conn: asyncpg.Connection, batch: IngestBatchResult
    ) -> IngestBatchResult:
        if batch.audit_event_id is None:
            raise ValueError(
                "md_ingest_batch.audit_event_id는 NOT NULL이다(§4.1 fail-closed) — "
                "감사 이벤트 없이 배치를 기록할 수 없다"
            )

        record_count = (
            batch.verdict.accepted + batch.verdict.quarantined + batch.verdict.rejected
        )
        try:
            await conn.execute(
                "INSERT INTO md_ingest_batch "
                "(id, tenant_id, source, venue, instrument_id, timeframe, range_start, "
                " range_end, request_fingerprint, batch_hash, record_count, verdict, "
                " audit_event_id) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)",
                batch.batch_id,
                batch.tenant_id,
                batch.source,
                batch.venue.value,
                batch.instrument_id,
                batch.timeframe.value,
                batch.range_start,
                batch.range_end,
                batch.request_fingerprint,
                batch.batch_hash,
                record_count,
                batch.verdict.verdict.value,
                batch.audit_event_id,
            )
        except asyncpg.exceptions.UniqueViolationError as exc:
            raise DuplicateBatchError(f"이미 존재하는 batch_id: {batch.batch_id}") from exc

        return batch

    async def add_issues(
        self, conn: asyncpg.Connection, batch_id: UUID, issues: list[QualityIssue]
    ) -> None:
        if not issues:
            return
        await conn.executemany(
            "INSERT INTO md_quality_issue (batch_id, type, severity, open_time, detail) "
            "VALUES ($1, $2, $3, $4, $5::jsonb)",
            [
                (
                    batch_id,
                    issue.type.value,
                    issue.severity.value,
                    issue.open_time,
                    json.dumps(issue.detail),
                )
                for issue in issues
            ],
        )

    async def get(self, conn: asyncpg.Connection, batch_id: UUID) -> IngestBatchResult | None:
        row = await conn.fetchrow("SELECT * FROM md_ingest_batch WHERE id = $1", batch_id)
        if row is None:
            return None

        verdict = await _reconstruct_verdict(conn, row)
        stored_range = await _stored_range(conn, batch_id)

        return IngestBatchResult(
            batch_id=row["id"],
            tenant_id=row["tenant_id"],
            source=row["source"],
            venue=Venue(row["venue"]),
            instrument_id=row["instrument_id"],
            timeframe=Timeframe(row["timeframe"]),
            range_start=row["range_start"],
            range_end=row["range_end"],
            request_fingerprint=row["request_fingerprint"],
            verdict=verdict,
            batch_hash=row["batch_hash"],
            audit_event_id=row["audit_event_id"],
            stored_range=stored_range,
        )
