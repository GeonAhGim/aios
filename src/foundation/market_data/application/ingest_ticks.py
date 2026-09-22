"""LA-16 — Tick ingest: trade_id monotonicity and time-regression check → store → audit.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9.2 LA-16.

Unlike candles (LA-15), there are no session/gap checks (ticks are a 24-hour stream) — the
only gate is that trade_id and traded_at do not go backward for the same (venue,
instrument_id). On violation, the entire batch is REJECTED (no partial storage, §9 DoD) —
ticks have no isolation table (LA-11), so individual record isolation like candles is not
possible.

`contracts/v1.py`, migrations, and `BatchRepository` are immutable (task-842 decision).
`IngestSource` has no tick lookup method and this leaf adds none, so the caller passes a
pre-filled list of `TickRecord` objects with the correct `instrument_id` already set (no
re-keying). `IngestTicksCommand` is therefore not a public contract but a module-private
input — symbol status checks, as-of lookups, and other reference-data dependencies are
out of scope.

Applying "reject trade_id at or below last stored" (§9) literally (including equality)
would break replay idempotency (re-fetching the same batch succeeds silently via `md_tick`
ON CONFLICT DO NOTHING, same principle as LA-15 candle re-fetch) — instead, only ticks
whose (trade_id, traded_at) composite key already matches the UNIQUE constraint on
`md_tick` are treated as "already fetched" and excluded from the regression check. For
ticks whose composite key is still absent, we reject as regression only if the value is
less than the maximum seen so far (including last stored). If we judged "already known"
by trade_id alone, a re-fetch with the same trade_id but a different traded_at would
bypass the regression check (review REJECT, task-1302).

The transaction boundary bundles storage, batch recording, and audit events into one —
on audit failure, everything rolls back (same as LA-15). To prevent concurrent calls for
the same (venue, instrument_id) from reading the same "last stored" value and both
passing, we serialize with `pg_advisory_xact_lock` until the transaction ends.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

import asyncpg

from src.foundation.evidence.domain.models import Classification, Outcome
from src.foundation.evidence.domain.rules import assert_safe_payload, compute_payload_hash
from src.foundation.market_data.application.ingest_candles import AuditAppender
from src.foundation.market_data.contracts.v1 import (
    QualityIssue,
    QualityIssueType,
    QualityVerdict,
    Severity,
    TickIngestBatchResult,
    TickRecord,
    Venue,
    Verdict,
)
from src.foundation.market_data.domain.lineage import batch_hash, request_fingerprint
from src.foundation.market_data.ports.batch_repository import BatchRepository

__all__ = ["IngestTicksCommand", "ingest_ticks"]


@dataclass(frozen=True)
class IngestTicksCommand:
    """`ticks` must be a non-empty list of the same (venue, instrument_id), already
    filled with the correct `instrument_id` (see module docstring)."""

    tenant_id: UUID | None
    source: str
    ticks: list[TickRecord]
    trace_id: UUID


def _parse_trade_id(trade_id: str) -> int | None:
    """Non-numeric trade_id (exchange format unvalidated) returns `None` — at this
    point the trade_id axis is skipped and only the time axis determines regression."""
    try:
        return int(trade_id)
    except ValueError:
        return None


async def _last_stored(
    conn: asyncpg.Connection, venue: Venue, instrument_id: UUID
) -> tuple[int | None, datetime | None]:
    """Returns the maximum `traded_at` and, among all trade_ids for that timestamp
    (concurrent fills), the maximum trade_id as baseline. Concurrent fills stored in
    the same transaction share the same `created_at` (Postgres `now()` is fixed at
    transaction start), so `traded_at DESC, created_at DESC LIMIT 1` is non-deterministic
    and could pick a smaller trade_id as baseline — allowing a genuinely regressive batch
    to pass (QA finding, task-1004)."""
    max_traded_at = await conn.fetchval(
        "SELECT MAX(traded_at) FROM md_tick WHERE venue = $1 AND instrument_id = $2",
        venue.value,
        instrument_id,
    )
    if max_traded_at is None:
        return None, None
    rows = await conn.fetch(
        "SELECT trade_id FROM md_tick WHERE venue = $1 AND instrument_id = $2 AND traded_at = $3",
        venue.value,
        instrument_id,
        max_traded_at,
    )
    max_trade_id: int | None = None
    for row in rows:
        parsed = _parse_trade_id(row["trade_id"])
        if parsed is not None and (max_trade_id is None or parsed > max_trade_id):
            max_trade_id = parsed
    return max_trade_id, max_traded_at


async def _known_ticks(
    conn: asyncpg.Connection, venue: Venue, instrument_id: UUID, ticks: list[TickRecord]
) -> set[tuple[str, datetime]]:
    """Determines "already stored" by the composite key matching
    `md_tick` UNIQUE(venue, instrument_id, trade_id, traded_at). Judging by
    trade_id alone would misclassify re-fetches with the same trade_id but a
    different traded_at as duplicates, causing them to skip the regression check
    and letting regressive batches bypass rejection (review REJECT, task-1302)."""
    rows = await conn.fetch(
        "SELECT trade_id, traded_at FROM md_tick WHERE venue = $1 AND instrument_id = $2 "
        "AND trade_id = ANY($3::text[])",
        venue.value,
        instrument_id,
        [t.trade_id for t in ticks],
    )
    return {(row["trade_id"], row["traded_at"]) for row in rows}


def _first_regression(
    ticks: list[TickRecord],
    baseline_trade_id: int | None,
    baseline_traded_at: datetime | None,
) -> QualityIssue | None:
    """Finds, in order, the first point where trade_id/traded_at strictly decreases
    relative to the running maximum (including baseline). Equality does not update
    the maximum but is not a violation."""
    max_trade_id = baseline_trade_id
    max_traded_at = baseline_traded_at
    for tick in ticks:
        if max_traded_at is not None and tick.traded_at < max_traded_at:
            return QualityIssue(
                type=QualityIssueType.TIME_MISALIGNED,
                severity=Severity.REJECT,
                open_time=tick.traded_at,
                detail={"reason": "traded_at_regression", "trade_id": tick.trade_id},
            )
        trade_id_int = _parse_trade_id(tick.trade_id)
        if trade_id_int is not None and max_trade_id is not None and trade_id_int < max_trade_id:
            return QualityIssue(
                type=QualityIssueType.TIME_MISALIGNED,
                severity=Severity.REJECT,
                open_time=tick.traded_at,
                detail={"reason": "trade_id_regression", "trade_id": tick.trade_id},
            )
        if max_traded_at is None or tick.traded_at > max_traded_at:
            max_traded_at = tick.traded_at
        if trade_id_int is not None and (max_trade_id is None or trade_id_int > max_trade_id):
            max_trade_id = trade_id_int
    return None


async def _store_ticks(conn: asyncpg.Connection, ticks: list[TickRecord]) -> None:
    await conn.executemany(
        "INSERT INTO md_tick (venue, instrument_id, trade_id, price, quantity, side, traded_at) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7) "
        "ON CONFLICT (venue, instrument_id, trade_id, traded_at) DO NOTHING",
        [
            (t.venue.value, t.instrument_id, t.trade_id, t.price, t.quantity, t.side, t.traded_at)
            for t in ticks
        ],
    )


async def ingest_ticks(
    cmd: IngestTicksCommand,
    *,
    batches: BatchRepository,
    audit: AuditAppender,
    pool: asyncpg.Pool,
) -> TickIngestBatchResult:
    if not cmd.ticks:
        raise ValueError("Empty tick batch cannot be processed")

    venue = cmd.ticks[0].venue
    instrument_id = cmd.ticks[0].instrument_id
    range_start = min(t.traded_at for t in cmd.ticks)
    range_end = max(t.traded_at for t in cmd.ticks)
    hash_of_batch = batch_hash(cmd.ticks)
    fingerprint = request_fingerprint(
        cmd.source,
        {
            "venue": venue.value,
            "instrument_id": str(instrument_id),
            "range_start": range_start.isoformat(),
            "range_end": range_end.isoformat(),
            "batch_hash": hash_of_batch,
        },
    )
    batch_id = uuid4()

    async with pool.acquire() as conn, conn.transaction():
        # Prevent race on "last stored" value for concurrent batches (see module docstring).
        await conn.execute(
            "SELECT pg_advisory_xact_lock(hashtext($1))", f"{venue.value}:{instrument_id}"
        )
        known = await _known_ticks(conn, venue, instrument_id, cmd.ticks)
        new_ticks = [t for t in cmd.ticks if (t.trade_id, t.traded_at) not in known]
        baseline_trade_id, baseline_traded_at = await _last_stored(conn, venue, instrument_id)
        regression = _first_regression(new_ticks, baseline_trade_id, baseline_traded_at)
        is_stored = regression is None

        verdict = QualityVerdict(
            verdict=Verdict.ACCEPT if is_stored else Verdict.REJECT,
            accepted=len(cmd.ticks) if is_stored else 0,
            quarantined=0,
            rejected=0 if is_stored else len(cmd.ticks),
            issues=[] if regression is None else [regression],
        )

        payload: dict[str, object] = {
            "batch_id": str(batch_id),
            "venue": venue.value,
            "instrument_id": str(instrument_id),
            "range_start": range_start.isoformat(),
            "range_end": range_end.isoformat(),
            "verdict": verdict.verdict.value,
            "accepted": verdict.accepted,
            "rejected": verdict.rejected,
        }
        assert_safe_payload(payload)
        event = await audit.append_event_in(
            conn,
            tenant_id=cmd.tenant_id,
            aggregate_type="md_ingest_batch_tick",
            aggregate_id=batch_id,
            aggregate_revision=None,
            action="market_data.ticks_ingested",
            outcome=Outcome.SUCCESS if is_stored else Outcome.DENIED,
            actor_subject_id=None,
            trace_id=cmd.trace_id,
            payload_hash=compute_payload_hash(payload),
            payload=payload,
            classification=Classification.INTERNAL,
        )

        result = TickIngestBatchResult(
            batch_id=batch_id,
            tenant_id=cmd.tenant_id,
            source=cmd.source,
            venue=venue,
            instrument_id=instrument_id,
            range_start=range_start,
            range_end=range_end,
            request_fingerprint=fingerprint,
            verdict=verdict,
            batch_hash=hash_of_batch,
            audit_event_id=event.id,
        )
        created = await batches.create_tick_batch(conn, result)

        if is_stored:
            await _store_ticks(conn, cmd.ticks)

    return created
