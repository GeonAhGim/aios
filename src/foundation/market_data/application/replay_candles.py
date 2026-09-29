"""LA-17 — deterministic replay for backtesting (strict gap check, hash determinism).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.2, §9.2 LA-17, A5.

Query/adjustment/gap-detection core is delegated to
`application/get_candles.load_series` (same leaf) — not reimplemented here.
The only thing this file adds is the strict judgment: if even one candle is
missing against the expected open_time, it does not return a `ReplaySeries`
and instead raises `ReplayIncompleteError` (`MD_REPLAY_INCOMPLETE`).
`series_hash` always comes out the same for the same candle set regardless of
storage-row insertion order or partition distribution, because
`domain/lineage.batch_hash` (LA-8) hashes sorted canonical JSON
(A5 "same as_of + same range -> same bytes").
"""

from __future__ import annotations

from datetime import datetime, timezone

import asyncpg
from pydantic import AwareDatetime

from src.foundation.market_data.application.get_candles import (
    UnknownSeriesError,
    ensure_as_of_not_future,
    load_series,
)
from src.foundation.market_data.contracts.v1 import ReplayRequest, ReplaySeries
from src.foundation.market_data.domain.lineage import batch_hash
from src.foundation.market_data.ports.calendar_repository import CalendarRepository
from src.foundation.market_data.ports.candle_store import CandleStore
from src.foundation.market_data.ports.reference_repository import ReferenceRepository

__all__ = ["ReplayIncompleteError", "UnknownSeriesError", "replay"]


class ReplayIncompleteError(Exception):
    """`MD_REPLAY_INCOMPLETE` — missing candles detected against expected
    open_time in strict mode. Not retryable (same input produces same gap) —
    must refill the gap and re-run."""

    def __init__(self, *, expected_count: int, missing_count: int) -> None:
        super().__init__(f"리플레이 불완전: expected={expected_count} missing={missing_count}")
        self.expected_count = expected_count
        self.missing_count = missing_count


async def replay(
    q: ReplayRequest,
    *,
    store: CandleStore,
    refs: ReferenceRepository,
    cal: CalendarRepository,
    pool: asyncpg.Pool,
    now: datetime | None = None,
) -> ReplaySeries:
    """§9.2 LA-17: identical `q.as_of` (required) and range produce a
    `series_hash` identical at the byte level on repeated calls. Quarantined
    candles never mix into the result because `CandleStore.query` does not
    query the quarantine table in the first place
    (`ReplayRequest.include_quarantined` is contractually always `False`).

    `now` is an injectable clock (defaults to the wall clock) so callers that
    already hold a trusted "current time" (e.g. the same timestamp `q.as_of`
    was derived from) can pass it instead of racing a second, independent
    `datetime.now(timezone.utc)` read against a DB-server-clock `as_of` —
    two different clock sources can disagree by sub-millisecond amounts and
    spuriously trip `AsOfInFutureError`."""
    ensure_as_of_not_future(q.as_of, now if now is not None else datetime.now(timezone.utc))

    async with pool.acquire() as conn:
        candles, issues, expected_total = await load_series(
            q, store=store, refs=refs, cal=cal, conn=conn, now=q.as_of
        )

    missing_count = len(issues)
    if missing_count:
        raise ReplayIncompleteError(expected_count=expected_total, missing_count=missing_count)

    gaps: list[tuple[AwareDatetime, AwareDatetime]] = []
    return ReplaySeries(
        key=q.key,
        candles=candles,
        gaps=gaps,
        adjustment=q.adjustment,
        as_of=q.as_of,
        series_hash=batch_hash(candles),
        expected_count=expected_total,
        missing_count=missing_count,
    )
