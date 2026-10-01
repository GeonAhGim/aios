"""LA-13 — asyncpg implementation of `BatchRepository` (ports/batch_repository.py).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.2, §5, §9.2 LA-13.

`create()` moves an `IngestBatchResult` (LA-9 port signature, extended with the
fields added by the task-615 note) into `md_ingest_batch` as a plain INSERT only
(re-inserting the same `batch_id` hits a PK violation -> `DuplicateBatchError`).
`md_ingest_batch.audit_event_id` is NOT NULL (§4.1 fail-closed — every batch
verdict must emit an audit event), so if `batch.audit_event_id` is `None` this
rejects before the DB call, to fail fast with a clearer error.

`get()` has one wrinkle: `md_ingest_batch` only stores `record_count` (the
total) and does not store the original 3-way split of
`QualityVerdict.accepted/quarantined/rejected` (that's just how the columns
LA-11 actually created turned out — this leaf cannot add a new migration, per
the task note). So `accepted` is recomputed from the row count actually stored
under that `batch_id` in `md_candle`, `quarantined` from the row count in
`md_quarantine_candle`, and `rejected` is derived as
`record_count - accepted - quarantined` (assuming both tables are append-only
and therefore unchanging — true as long as `create()` wrote `record_count`
correctly). `stored_range` is likewise recomputed from
`MIN/MAX(open_time)` on `md_candle` since there's no column for it either.
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
    TickIngestBatchResult,
    Timeframe,
    Venue,
    Verdict,
)

__all__ = ["DuplicateBatchError", "PostgresBatchRepository"]


class DuplicateBatchError(Exception):
    """`create()` called again with a `batch_id` that already exists — `md_ingest_batch`
    is INSERT only, so this is an explicit rejection, not an update."""


async def _reconstruct_verdict(conn: asyncpg.Connection, row: asyncpg.Record) -> QualityVerdict:
    accepted = await conn.fetchval("SELECT COUNT(*) FROM md_candle WHERE batch_id = $1", row["id"])
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
    return cast("tuple[AwareDatetime, AwareDatetime]", (row["range_start"], row["range_end"]))


class PostgresBatchRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def create(self, conn: asyncpg.Connection, batch: IngestBatchResult) -> IngestBatchResult:
        if batch.audit_event_id is None:
            raise ValueError(
                "md_ingest_batch.audit_event_id는 NOT NULL이다(§4.1 fail-closed) — "
                "감사 이벤트 없이 배치를 기록할 수 없다"
            )

        record_count = batch.verdict.accepted + batch.verdict.quarantined + batch.verdict.rejected
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

    async def get(
        self, conn: asyncpg.Connection, batch_id: UUID, tenant_id: UUID | None
    ) -> IngestBatchResult | None:
        # `IS NOT DISTINCT FROM` is needed because `tenant_id` can be NULL
        # (a platform-shared batch) and still has to compare as equal — `=`
        # always evaluates to NULL against NULL (SQL three-valued logic),
        # which would filter out the legitimate call (tenant_id=None) trying
        # to look up a shared batch as its own. A tenant mismatch and "no
        # such row" are both collapsed into the same `None` return, so
        # existence is never leaked.
        row = await conn.fetchrow(
            "SELECT * FROM md_ingest_batch WHERE id = $1 AND tenant_id IS NOT DISTINCT FROM $2",
            batch_id,
            tenant_id,
        )
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

    async def create_tick_batch(
        self, conn: asyncpg.Connection, batch: TickIngestBatchResult
    ) -> TickIngestBatchResult:
        if batch.audit_event_id is None:
            raise ValueError(
                "md_ingest_batch_tick.audit_event_id는 NOT NULL이다(§4.1 fail-closed) — "
                "감사 이벤트 없이 배치를 기록할 수 없다"
            )

        try:
            await conn.execute(
                "INSERT INTO md_ingest_batch_tick "
                "(id, tenant_id, source, venue, instrument_id, range_start, range_end, "
                " request_fingerprint, batch_hash, accepted_count, quarantined_count, "
                " rejected_count, verdict, issues, audit_event_id) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14::jsonb,$15)",
                batch.batch_id,
                batch.tenant_id,
                batch.source,
                batch.venue.value,
                batch.instrument_id,
                batch.range_start,
                batch.range_end,
                batch.request_fingerprint,
                batch.batch_hash,
                batch.verdict.accepted,
                batch.verdict.quarantined,
                batch.verdict.rejected,
                batch.verdict.verdict.value,
                json.dumps([issue.model_dump(mode="json") for issue in batch.verdict.issues]),
                batch.audit_event_id,
            )
        except asyncpg.exceptions.UniqueViolationError as exc:
            raise DuplicateBatchError(f"이미 존재하는 batch_id: {batch.batch_id}") from exc

        return batch

    async def get_tick_batch(
        self, conn: asyncpg.Connection, batch_id: UUID, tenant_id: UUID | None
    ) -> TickIngestBatchResult | None:
        # Same `IS NOT DISTINCT FROM` tenant-isolation reasoning as get().
        row = await conn.fetchrow(
            "SELECT * FROM md_ingest_batch_tick "
            "WHERE id = $1 AND tenant_id IS NOT DISTINCT FROM $2",
            batch_id,
            tenant_id,
        )
        if row is None:
            return None

        issues = [QualityIssue.model_validate(issue) for issue in json.loads(row["issues"])]
        verdict = QualityVerdict(
            verdict=Verdict(row["verdict"]),
            accepted=row["accepted_count"],
            quarantined=row["quarantined_count"],
            rejected=row["rejected_count"],
            issues=issues,
        )

        return TickIngestBatchResult(
            batch_id=row["id"],
            tenant_id=row["tenant_id"],
            source=row["source"],
            venue=Venue(row["venue"]),
            instrument_id=row["instrument_id"],
            range_start=row["range_start"],
            range_end=row["range_end"],
            request_fingerprint=row["request_fingerprint"],
            verdict=verdict,
            batch_hash=row["batch_hash"],
            audit_event_id=row["audit_event_id"],
        )
