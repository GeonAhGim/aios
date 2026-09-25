"""UX-6 — screener execution: universe scan, cursor pagination, result cap, cache.

Spec: docs/specs/L4_product_experience_and_discovery_v1.0.md §2.2
`application/run_screen.py`("execution (cursor pagination/cap), result cache"),
§9 UX-6 DoD("1,000 rows <=10s, cross-tenant 404").

Cross-tenant 404 (`run_saved_screen`): a screen run is only tenant-scoped when
it is run *by saved-screener id* — `SavedScreenerRepository.get()` (UX-5/U-1a,
already built) already folds "exists but belongs to another tenant" into
`None` the same way as every other cross-tenant read in this codebase; this
module does not re-decide that, it just relays `None` to the caller (who maps
it to 404, same convention as the repository's own docstring).

Result cache: caches the whole matched+sorted result set (bounded at
`MAX_RESULT_ROWS`) behind a hash of the `ScreenDefinition`, not per-page — a
second page request for the same definition within the TTL reuses the first
page's scan instead of re-touching the universe index.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from src.foundation.market_data.contracts.v1 import InstrumentRef, Venue
from src.foundation.screener.contracts.v1 import ScreenDefinition
from src.foundation.screener.domain.evaluate import matches_filters, required_field_names, sort_key
from src.foundation.screener.domain.evaluate import validate_plan_supported as _validate_plan
from src.foundation.screener.domain.query_plan import QueryPlan, build_query_plan
from src.foundation.screener.ports.field_source import ScreenerFieldSource
from src.foundation.screener.ports.repository import SavedScreenerRepository

__all__ = [
    "DEFAULT_PAGE_SIZE",
    "MAX_RESULT_ROWS",
    "SCAN_TIMEOUT_SECONDS",
    "ScreenCursorError",
    "ScreenLimitExceededError",
    "ScreenResultCache",
    "ScreenResultRow",
    "ScreenRunPage",
    "ScreenTimeoutError",
    "ScreenUniverseError",
    "run_saved_screen",
    "run_screen",
]

MAX_RESULT_ROWS = 1000  # spec §9 UX-6 DoD: 1,000-row cap
SCAN_TIMEOUT_SECONDS = 10.0  # spec §9 UX-6 DoD: <=10s / §3 UX_SCREEN_TIMEOUT
DEFAULT_PAGE_SIZE = 100
_UNIVERSE_SCAN_BATCH = 200
_CACHE_TTL_SECONDS = 30.0


class ScreenUniverseError(Exception):
    """`ScreenDefinition.universe` is not a known venue (or comma-separated list of
    venues) / "ALL" — 400."""


class ScreenCursorError(Exception):
    """Opaque cursor string does not decode to a valid page offset — 400."""


class ScreenLimitExceededError(Exception):
    """`page_size` outside `[1, MAX_RESULT_ROWS]` — §3 `UX_SCREEN_LIMIT`(413)."""


class ScreenTimeoutError(Exception):
    """Universe scan exceeded `SCAN_TIMEOUT_SECONDS` — §3 `UX_SCREEN_TIMEOUT`(408)."""


@dataclass(frozen=True, slots=True)
class ScreenResultRow:
    instrument_id: UUID
    symbol: str
    venue: Venue
    values: dict[str, Decimal]


@dataclass(frozen=True, slots=True)
class ScreenRunPage:
    rows: tuple[ScreenResultRow, ...]
    total: int
    truncated: bool
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class _CacheEntry:
    rows: tuple[ScreenResultRow, ...]
    truncated: bool
    expires_at: float


class ScreenResultCache:
    """Process-local TTL cache, `ScreenDefinition` hash -> full matched result set.

    Not shared across workers/processes — acceptable for this leaf (a stale hit
    only ever serves a slightly-older-than-TTL snapshot, never another
    tenant's data, since the key is derived from the full definition and
    `run_saved_screen`'s tenant check happens before this cache is ever
    consulted)."""

    def __init__(self, ttl_seconds: float = _CACHE_TTL_SECONDS) -> None:
        self._ttl = ttl_seconds
        self._entries: dict[str, _CacheEntry] = {}

    def get(self, key: str) -> _CacheEntry | None:
        entry = self._entries.get(key)
        if entry is None or entry.expires_at <= time.monotonic():
            return None
        return entry

    def set(self, key: str, rows: tuple[ScreenResultRow, ...], *, truncated: bool) -> None:
        self._entries[key] = _CacheEntry(
            rows=rows, truncated=truncated, expires_at=time.monotonic() + self._ttl
        )


def _cache_key(definition: ScreenDefinition) -> str:
    payload = json.dumps(definition.model_dump(mode="json"), sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _parse_universe(universe: str) -> frozenset[Venue]:
    raw = universe.strip()
    if not raw:
        raise ScreenUniverseError("universe must not be empty")
    if raw.upper() == "ALL":
        return frozenset(Venue)
    names = [part.strip().upper() for part in raw.split(",") if part.strip()]
    try:
        return frozenset(Venue(name) for name in names)
    except ValueError as exc:
        raise ScreenUniverseError(f"unknown venue in universe: {raw!r}") from exc


def _decode_cursor(cursor: str | None) -> int:
    if cursor is None:
        return 0
    try:
        offset = int(cursor)
    except ValueError as exc:
        raise ScreenCursorError(f"invalid cursor: {cursor!r}") from exc
    if offset < 0:
        raise ScreenCursorError(f"invalid cursor: {cursor!r}")
    return offset


def _sorted_matches(
    plan: QueryPlan, matches: list[tuple[InstrumentRef, dict[str, Decimal]]]
) -> list[tuple[InstrumentRef, dict[str, Decimal]]]:
    if plan.sort_field is None:
        return matches
    reverse = plan.sort_direction == "desc"

    def _key(pair: tuple[InstrumentRef, dict[str, Decimal]]) -> Decimal:
        value = sort_key(plan, pair[1])
        if value is None:
            # `sort_field` is always in `required` and the scan already filtered
            # out rows missing any required field — this would be an internal bug.
            raise AssertionError(f"sort_field missing on a matched row: {pair[0].instrument_id}")
        return value

    return sorted(matches, key=_key, reverse=reverse)


async def _scan_universe(
    plan: QueryPlan, venues: frozenset[Venue], field_source: ScreenerFieldSource, as_of: datetime
) -> tuple[tuple[ScreenResultRow, ...], bool]:
    required = required_field_names(plan)
    matches: list[tuple[InstrumentRef, dict[str, Decimal]]] = []
    after: UUID | None = None
    truncated = False
    while True:
        page = await field_source.universe_page(
            venues=venues, after=after, limit=_UNIVERSE_SCAN_BATCH
        )
        if not page:
            break
        by_venue: dict[Venue, list[UUID]] = defaultdict(list)
        for ref in page:
            by_venue[ref.venue].append(ref.instrument_id)
        field_values = await field_source.read_fields(
            instrument_ids_by_venue=by_venue, field_names=required, as_of=as_of
        )
        for ref in page:
            row = field_values.get(ref.instrument_id)
            if row is None or not required.issubset(row.keys()):
                continue
            if matches_filters(plan, row):
                matches.append((ref, row))
                if len(matches) >= MAX_RESULT_ROWS:
                    truncated = True
                    break
        after = page[-1].instrument_id
        if truncated or len(page) < _UNIVERSE_SCAN_BATCH:
            break
    ordered = _sorted_matches(plan, matches)
    rows = tuple(
        ScreenResultRow(
            instrument_id=ref.instrument_id,
            symbol=ref.canonical_symbol,
            venue=ref.venue,
            values=dict(row),
        )
        for ref, row in ordered
    )
    return rows, truncated


async def run_screen(
    definition: ScreenDefinition,
    *,
    field_source: ScreenerFieldSource,
    cache: ScreenResultCache,
    cursor: str | None = None,
    page_size: int = DEFAULT_PAGE_SIZE,
    timeout_seconds: float = SCAN_TIMEOUT_SECONDS,
) -> ScreenRunPage:
    if page_size <= 0 or page_size > MAX_RESULT_ROWS:
        raise ScreenLimitExceededError(f"page_size must be in [1, {MAX_RESULT_ROWS}]")
    offset = _decode_cursor(cursor)

    plan = build_query_plan(definition)
    _validate_plan(plan)
    venues = _parse_universe(plan.universe)

    key = _cache_key(definition)
    cached = cache.get(key)
    if cached is not None:
        rows, truncated = cached.rows, cached.truncated
    else:
        try:
            rows, truncated = await asyncio.wait_for(
                _scan_universe(plan, venues, field_source, datetime.now(timezone.utc)),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            raise ScreenTimeoutError(
                f"screen scan exceeded {timeout_seconds}s (§3 UX_SCREEN_TIMEOUT)"
            ) from exc
        cache.set(key, rows, truncated=truncated)

    page_rows = rows[offset : offset + page_size]
    next_offset = offset + page_size
    next_cursor = str(next_offset) if next_offset < len(rows) else None
    return ScreenRunPage(
        rows=page_rows, total=len(rows), truncated=truncated, next_cursor=next_cursor
    )


async def run_saved_screen(
    tenant_id: UUID,
    screener_id: UUID,
    *,
    repo: SavedScreenerRepository,
    field_source: ScreenerFieldSource,
    cache: ScreenResultCache,
    cursor: str | None = None,
    page_size: int = DEFAULT_PAGE_SIZE,
    timeout_seconds: float = SCAN_TIMEOUT_SECONDS,
) -> ScreenRunPage | None:
    """`None` means "no such screener for this tenant" (cross-tenant read included,
    see the module docstring) — the caller maps it to 404, it is never raised as
    an exception here."""
    saved = await repo.get(tenant_id, screener_id)
    if saved is None:
        return None
    return await run_screen(
        saved.definition,
        field_source=field_source,
        cache=cache,
        cursor=cursor,
        page_size=page_size,
        timeout_seconds=timeout_seconds,
    )
