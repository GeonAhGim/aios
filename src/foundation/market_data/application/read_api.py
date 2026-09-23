"""LA-24 — Identifier resolution, entitlement adjudication, and pagination used by
the HTTP read API (pure code + port calls).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9.2 LA-24.

Split out from `src/api/routers/market_data.py` which hits the 300-line cap
(P6.line_cap); HTTP-transmission-irrelevant logic lives here. No SQL or HTTP —
only port calls (`ReferenceRepository`/`ReferenceReadRepository`/`EntitlementPort`/
`VenueRegistrySource`). The three exceptions are mapped to status codes by
`exception_registry_foundation.py` (404/409/400).

Identifier rule (spec: "both allowed, both returned"): when `instrument_id`
(md_instrument UUID) is present it takes priority and `venue` is only validated
for consistency; when absent, `symbol` (venue symbol) is resolved via
`md_symbol_alias` within its validity window. Unregistered symbols, venue
mismatches, and entitlement denials all fold into the **same**
`MarketDataNotFoundError` (tenant-agnostic 404).

DC-28 (ADR-2026-09-06-H D2) — `authorize_redistribution()` applies the same
principle this file already uses ("entitlement denial is indistinguishable
from nonexistence") to redistribution scope as well: if the source contract
is missing (D2 "unspecified is treated as NONE") or does not permit this
use, it folds into the same `MarketDataNotFoundError`. The pure
determination (`permits_use`) belongs to
`domain/entitlement/source_contract.py` (DC-27), and the port call
(`authorize_source_access`) is not reimplemented either — this function
just combines the two into a single gate shared by the enforcement points
(read API, chart, backtest, export).
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from uuid import UUID

import asyncpg

from src.foundation.market_data.application.authorize_source_access import (
    authorize_source_access,
)
from src.foundation.market_data.contracts.v1 import (
    CandleRecord,
    InstrumentRef,
    Timeframe,
    Venue,
)
from src.foundation.market_data.domain.entitlement.policy import (
    Entitlement,
    EntitlementSubject,
    FeedRequest,
)
from src.foundation.market_data.domain.entitlement.source_contract import DataUse, permits_use
from src.foundation.market_data.ports.entitlement import EntitlementPort, VenueRegistrySource
from src.foundation.market_data.ports.reference_repository import (
    ReferenceReadRepository,
    ReferenceRepository,
)
from src.foundation.market_data.ports.source_contract_repository import SourceContractRepository

__all__ = [
    "DataCoverageMissingError",
    "MarketDataNotFoundError",
    "MarketDataQueryError",
    "authorize_feed",
    "authorize_redistribution",
    "authorize_venue",
    "paginate_candles",
    "resolve_instrument",
    "validate_span",
]

_NOT_FOUND_MESSAGE = "인스트루먼트를 찾을 수 없습니다."


class MarketDataNotFoundError(Exception):
    """Unregistered symbol/instrument_id, venue mismatch, and entitlement denial
    (different tenant) all fold into the same 404 — never leak existence via status code."""


class DataCoverageMissingError(Exception):
    """Requested range falls outside stored coverage (expected sessions exist but zero stored candles).
    §4.1 prohibits 0/NaN filling → 409 `DATA_COVERAGE_MISSING`."""


class MarketDataQueryError(Exception):
    """Invalid query parameter combination (missing identifier, naive datetime, start ≥ end) — 400."""


def _require_aware(name: str, value: datetime | None) -> None:
    if value is not None and value.tzinfo is None:
        raise MarketDataQueryError(f"{name}은(는) tz-aware datetime(UTC)이어야 합니다.")


def validate_span(start: datetime, end: datetime, *extra: tuple[str, datetime | None]) -> None:
    _require_aware("start", start)
    _require_aware("end", end)
    for name, value in extra:
        _require_aware(name, value)
    if start >= end:
        raise MarketDataQueryError("start는 end보다 앞서야 합니다.")


def paginate_candles(
    candles: list[CandleRecord], cursor: datetime | None, limit: int
) -> tuple[list[CandleRecord], str | None]:
    """Pure — takes `limit` candles starting from the first where `open_time >= cursor`.
    Next cursor is that candle's `open_time` (ISO 8601), or None if no more. Reads the
    full series (request range) once and slices, so `as_of` snapshot determinism is
    preserved across pages."""
    first = 0
    if cursor is not None:
        first = next((i for i, c in enumerate(candles) if c.open_time >= cursor), len(candles))
    page = candles[first : first + limit]
    after = first + limit
    if after >= len(candles):
        return page, None
    # Align with datetime serialization (pydantic, `Z`) in response body.
    return page, candles[after].open_time.isoformat().replace("+00:00", "Z")


async def resolve_instrument(
    conn: asyncpg.Connection,
    *,
    refs: ReferenceRepository,
    reader: ReferenceReadRepository,
    venue: Venue | None,
    symbol: str | None,
    instrument_id: UUID | None,
    now: datetime,
) -> InstrumentRef:
    if instrument_id is None and symbol is None:
        raise MarketDataQueryError("symbol 또는 instrument_id 중 하나는 필요합니다.")
    if instrument_id is not None:
        by_id = await reader.get_by_id(conn, instrument_id)
        if by_id is None or (venue is not None and by_id.venue is not venue):
            raise MarketDataNotFoundError(_NOT_FOUND_MESSAGE)
        return by_id
    if venue is None or symbol is None:
        raise MarketDataQueryError("symbol로 조회하려면 venue가 필요합니다.")
    by_symbol = await refs.get_instrument(conn, venue, symbol, now)
    if by_symbol is None:
        raise MarketDataNotFoundError(_NOT_FOUND_MESSAGE)
    return by_symbol


async def authorize_feed(
    port: EntitlementPort, *, tenant_id: UUID, subject_id: UUID, inst: InstrumentRef,
    timeframe: Timeframe,
) -> Entitlement:
    """Candle feed queries the port across (venue, asset_class, instrument, timeframe) axes."""
    subject = EntitlementSubject(tenant_id=tenant_id, subject_id=subject_id, grants=())
    feed = FeedRequest(
        venue=inst.venue,
        asset_class=inst.asset_class,
        instrument_id=str(inst.instrument_id),
        timeframe=timeframe,
        want_realtime=False,
    )
    decision = await port.allowed(subject, feed)
    if not decision.allowed:
        raise MarketDataNotFoundError(_NOT_FOUND_MESSAGE)
    return decision


async def authorize_venue(
    source: VenueRegistrySource, *, tenant_id: UUID, inst: InstrumentRef
) -> None:
    """Reference data (listing, aliases) has no timeframe axis — registered per-venue."""
    if inst.venue not in await source.registered_venues(tenant_id):
        raise MarketDataNotFoundError(_NOT_FOUND_MESSAGE)


async def authorize_redistribution(
    conn: asyncpg.Connection,
    source_id: str,
    *,
    repo: SourceContractRepository,
    clock: Callable[[], datetime],
    use: DataUse,
) -> None:
    """D2's common gate for enforcement points (read API, chart, backtest,
    export).

    `source_id` refers to a per-venue source contract row (for market_data
    vendors, `Venue.value` is exactly `source_contract.source_id` — no
    separate mapping table is needed, the string the adapter already knows
    is used as-is). Whether the contract is missing or expired
    (`authorize_source_access`), or present but does not permit this `use`
    (`permits_use`), all cases fold into the same `MarketDataNotFoundError`
    — distinguishing the reason by status code would let a response alone
    reveal "this source does/doesn't have a contract" or "this tier isn't
    enough", the same existence-leak problem as `authorize_feed`."""
    grant = await authorize_source_access(conn, source_id, repo=repo, clock=clock)
    if not grant.allowed or grant.redistribution_scope is None:
        raise MarketDataNotFoundError(_NOT_FOUND_MESSAGE)
    if not permits_use(grant.redistribution_scope, use):
        raise MarketDataNotFoundError(_NOT_FOUND_MESSAGE)
