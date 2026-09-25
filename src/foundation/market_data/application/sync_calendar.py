"""LA-14 — venue trading calendar year-sync use case + 1:1 audit event.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9.2 LA-14, §10 R4.

YAML parsing is handled exclusively by LA-12
`adapters/yaml_calendar_source.load_calendar` (do not reimplement) — the
caller pre-builds the result (`days`) and passes it here using the
scaffold signature `sync_calendar(venue, year, days, *, cal, audit, pool)`
unchanged. `CalendarRepository.upsert_days` (LA-12) is already idempotent
at `(venue, trade_date)` level (`ON CONFLICT ... DO UPDATE`), so this
function adds only a single audit event within a transaction boundary on
top of that.

`upsert_days` itself rejects `day.venue != venue` mismatches (adapter
`ValueError`), but this function performs that validation **before** the
transaction and audit event (fail-fast) so a bad argument does not hold
the connection idle — nothing was written, so there is no audit event
(natural consequence of §9 LA-14 "1:1 audit event": no event for a write
that was never attempted).

`md_venue_calendar_day` has no self-contained UUID PK (composite key of
venue+trade_date, LA-10). Audit events require `aggregate_id: UUID` (Sec.
1, item 79), so we treat the sync "run" itself as one aggregate and use a
UUID5 derived deterministically from `(venue, year)` — the same
aggregate_id is produced for every `(venue, year)` pair, allowing the
audit history for that venue and year to be tracked under a single id.
"""
from __future__ import annotations

import uuid
from typing import Protocol
from uuid import UUID

import asyncpg

from src.foundation.evidence.api import (
    AuditEvent,
    Classification,
    Outcome,
    assert_safe_payload,
    compute_payload_hash,
)
from src.foundation.market_data.contracts.v1 import CalendarDay, Venue
from src.foundation.market_data.ports.calendar_repository import CalendarRepository

__all__ = ["AuditAppender", "CalendarVenueMismatchError", "calendar_aggregate_id", "sync_calendar"]

_NAMESPACE = uuid.UUID("6f2a9d5e-0f0a-4b1a-9a3d-000000000000")


class CalendarVenueMismatchError(Exception):
    """`days` contains elements whose venue differs from the `venue` arg —
    catch this here with fail-closed before delegating to `upsert_days` so
    invalid data is never half-persisted (after entering a transaction)."""


class AuditAppender(Protocol):
    async def append_event_in(
        self,
        conn: asyncpg.Connection,
        *,
        tenant_id: UUID | None,
        aggregate_type: str,
        aggregate_id: UUID,
        aggregate_revision: int | None,
        action: str,
        outcome: Outcome,
        actor_subject_id: UUID | None,
        trace_id: UUID,
        payload_hash: str,
        payload: dict[str, object],
        classification: Classification,
    ) -> AuditEvent: ...


def calendar_aggregate_id(venue: Venue, year: int) -> UUID:
    """Deterministic UUID5 for (venue, year). Same args always produce the same id."""
    return uuid.uuid5(_NAMESPACE, f"{venue.value}:{year}")


async def sync_calendar(
    pool: asyncpg.Pool,
    venue: Venue,
    year: int,
    days: list[CalendarDay],
    *,
    actor_subject_id: UUID,
    trace_id: UUID,
    cal: CalendarRepository,
    audit: AuditAppender,
) -> int:
    in_year = [d for d in days if d.trade_date.year == year]
    for day in in_year:
        if day.venue is not venue:
            raise CalendarVenueMismatchError(
                f"venue 불일치: 인자={venue.value} day.venue={day.venue.value}"
            )

    async with pool.acquire() as conn, conn.transaction():
        await cal.upsert_days(conn, venue, in_year)

        payload: dict[str, object] = {
            "venue": venue.value,
            "year": year,
            "day_count": len(in_year),
            "sources": sorted({d.source for d in in_year}),
        }
        assert_safe_payload(payload)
        await audit.append_event_in(
            conn,
            tenant_id=None,
            aggregate_type="md_venue_calendar",
            aggregate_id=calendar_aggregate_id(venue, year),
            aggregate_revision=None,
            action="market_data.calendar_synced",
            outcome=Outcome.SUCCESS,
            actor_subject_id=actor_subject_id,
            trace_id=trace_id,
            payload_hash=compute_payload_hash(payload),
            payload=payload,
            classification=Classification.INTERNAL,
        )

    return len(in_year)
