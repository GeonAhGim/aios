"""FA-13 — conditional event-store append (hash chain).

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#§2.4 FA-13, §5.

Per the §5 table, "event append: `(stream_id, seq)` unique + conditional
insert" is this module's concurrency strategy. Unlike the advisory-lock
approach used by LB-9 (`positions/adapters/postgres_journal_repository.py`)
and evidence's `postgres_repository.py`, the event store has a very large
number of streams (one per order, per position, per journal entry), so
instead of sharing a global lock namespace it achieves fail-closed
serialization with just a `(stream_id, seq)` UNIQUE constraint plus a
conditional `WHERE` INSERT — the loser of a race is immediately asked to
retry via `SequenceConflictError` (no lock wait).

The hash chain's canonicalization rule reuses `canonical_json`
(`src/core/eventstore/canonical_json.py`, originally owned by the ledger's
LC-3 `hash_chain.py` but moved into core in task-6495 to fix a core-no-io
violation — foundation now re-exports it from this module) as-is (task-1703
decision — do not reimplement). The way `event_hash` concatenates fields and
hashes with sha256 rewrites the same recipe as the ledger's `entry_hash` for
the event's field set (the target fields differ) — the canonicalization
function itself was not duplicated.

This leaf does not create a migration (task-1703 decision: "if table DDL is
needed, don't bundle it into the same commit — ask via needs_decision").
The permanent `event_store` schema belongs to a separate leaf (before FA-14)
whose parent the PM will decide — this file only defines the table name
(`TABLE`) and the conditional SQL; real-DB integration tests verify it by
creating a temporary table inside their own transaction
(`tests/integration/core/eventstore/conftest.py`).

`conn` is the `asyncpg.Connection` the caller has already opened, passed
through as-is — this module does not do its own `pool.acquire` (LC-4/LB-11
precedent). Commit is the caller's responsibility.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

import asyncpg

from src.core.eventstore.canonical_json import canonical_json
from src.core.eventstore.contracts.v1 import DomainEvent

TABLE = "event_store"


class SequenceConflictError(Exception):
    """`ES_SEQUENCE_CONFLICT` — another append already claimed this `seq`
    (retryable). The caller must re-fetch the stream's latest seq and retry —
    this function does not absorb a resend itself (optimistic concurrency
    control, not idempotency-key REPLAY)."""

    def __init__(self, stream_id: str, expected_seq: int) -> None:
        super().__init__(
            f"stream_id={stream_id!r}: seq={expected_seq} is no longer the "
            "next seq (concurrent append conflict) — re-fetch the latest seq and retry."
        )
        self.stream_id = stream_id
        self.expected_seq = expected_seq


def payload_digest(payload: dict[str, Any]) -> str:
    """Deterministic digest of a payload. `canonical_json` (reused from LC-3)
    sorts keys, so the same payload always yields the same value regardless
    of Python dict ordering."""
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def event_hash(
    prev_hash: str | None,
    stream_id: str,
    seq: int,
    event_type: str,
    digest: str,
    occurred_at: datetime,
) -> str:
    """One link in the chain. If `prev_hash` is absent (the stream's first
    event), treat it as an empty string so the chain always starts
    deterministically (same recipe as LC-3's `entry_hash`)."""
    payload = "|".join(
        [prev_hash or "", stream_id, str(seq), event_type, digest, occurred_at.isoformat()]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _row_to_event(row: asyncpg.Record) -> DomainEvent:
    return DomainEvent(
        stream_id=row["stream_id"],
        seq=row["seq"],
        type=row["type"],
        payload=json.loads(row["payload"]),
        occurred_at=row["occurred_at"],
        recorded_at=row["recorded_at"],
        causation_id=row["causation_id"],
        correlation_id=row["correlation_id"],
        hash=row["hash"],
        prev_hash=row["prev_hash"],
    )


async def append(
    conn: asyncpg.Connection,
    *,
    stream_id: str,
    expected_seq: int,
    type: str,
    payload: dict[str, Any],
    occurred_at: datetime,
    recorded_at: datetime,
    causation_id: str | None = None,
    correlation_id: str | None = None,
) -> DomainEvent:
    """Conditionally append with `expected_seq`.

    `expected_seq` is the seq value this event must have (the stream's
    current head + 1). If the stream's actual head differs (a concurrent
    append already filled it, or the caller was looking at a stale head),
    raise `SequenceConflictError` — first filter against the head fetched
    before attempting the insert (saves a round trip), then, to also cover a
    race that slips in between the fetch and the INSERT, use both the
    conditional `WHERE` INSERT's 0-row RETURNING and the `(stream_id, seq)`
    UNIQUE violation as the final gate (fail-closed, no locking).
    """
    if occurred_at.tzinfo is None or recorded_at.tzinfo is None:
        raise ValueError("occurred_at/recorded_at must be tz-aware (UTC)")

    head = await conn.fetchrow(
        f"SELECT seq, hash FROM {TABLE} WHERE stream_id = $1 ORDER BY seq DESC LIMIT 1",  # noqa: S608
        stream_id,
    )
    last_seq: int = 0 if head is None else head["seq"]
    prev_hash: str | None = None if head is None else head["hash"]
    if expected_seq != last_seq + 1:
        raise SequenceConflictError(stream_id, expected_seq)

    digest = payload_digest(payload)
    new_hash = event_hash(prev_hash, stream_id, expected_seq, type, digest, occurred_at)

    try:
        row = await conn.fetchrow(
            f"INSERT INTO {TABLE} "  # noqa: S608 -- TABLE is a module constant, not user input
            "(stream_id, seq, type, payload, occurred_at, recorded_at, "
            " causation_id, correlation_id, hash, prev_hash) "
            "SELECT $1::varchar, $2::int, $3::varchar, $4::jsonb, $5::timestamptz, "
            "$6::timestamptz, $7::varchar, $8::varchar, $9::varchar, $10::varchar "
            f"WHERE COALESCE((SELECT MAX(seq) FROM {TABLE} WHERE stream_id = $1), 0) = $2 - 1 "
            "RETURNING *",
            stream_id,
            expected_seq,
            type,
            json.dumps(payload),
            occurred_at,
            recorded_at,
            causation_id,
            correlation_id,
            new_hash,
            prev_hash,
        )
    except asyncpg.UniqueViolationError as exc:
        raise SequenceConflictError(stream_id, expected_seq) from exc

    if row is None:
        raise SequenceConflictError(stream_id, expected_seq)

    return _row_to_event(row)
