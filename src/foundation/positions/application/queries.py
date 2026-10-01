"""LB-17 — read-only application layer for positions (pos_snapshot/pos_journal/pos_nav).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.3, §9.3 LB-17, LB-19.

Read-only: this module only delegates to `SnapshotRepository`/
`PositionJournalRepository`/`NavRepository` and performs no writes itself
(standard-71 §6). It returns LB-1 `contracts/v1.py`'s
`PositionSnapshotView`/`PositionJournalEntryView`/`NAVSnapshot` as-is
(field names unchanged) — the field names the frontend parser uses as SSOT
(task-628 decision, `frontend/packages/shared-types/src/positionView.ts`)
map 1:1 to this contract, so wrapping it in a separate view model here would
let the contract evolve independently in two places.

Tenant scoping (LB-19, PLT §3): `pos_journal.list_for` and
`pos_nav_daily.get` do not accept a tenant in their port contracts, so this
layer verifies ownership first — snapshots via
`SnapshotRepository.get(tenant_id, key)` (LB-18 cross_tenant fix), accounts
via `pos_account.tenant_id`. "Not found" and "owned by another tenant" are
not distinguished; both raise the same exception (no existence disclosure,
isomorphic to a 404). The `pos_account` lookup has no port in the §2 table,
so it is read here with a single SQL line — move it to an account
repository port if one is ever introduced.

FA-6 (docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-6, §9
table row 120): `list_positions` accepts an optional `portfolio_id` filter —
this is an existing query parameter, not a new API path. Scope resolution
reuses `resolve_portfolio_scope` from FA-5's
`entities/application/resolve_context.py` (no reimplementation). Calls that
do not supply `portfolio_id` behave byte-identically to before this
leaf."""

from __future__ import annotations

from datetime import date, timedelta
from uuid import UUID

import asyncpg

from src.foundation.entities.application.resolve_context import (
    EntityContextResolutionError,
    EntityRepository,
    resolve_portfolio_scope,
)
from src.foundation.positions.contracts.v1 import (
    NAVSnapshot,
    PositionJournalEntryView,
    PositionSnapshotView,
)
from src.foundation.positions.domain.position_key import InvalidPositionKeyError, PositionKey
from src.foundation.positions.ports.journal_repository import PositionJournalRepository
from src.foundation.positions.ports.nav_repository import NavRepository
from src.foundation.positions.ports.snapshot_repository import SnapshotRepository

__all__ = [
    "MAX_NAV_RANGE_DAYS",
    "NavRangeInvalidError",
    "PositionAccountNotFoundError",
    "PositionNotFoundError",
    "get_nav",
    "list_journal",
    "list_nav_range",
    "list_open_positions",
    "list_positions",
]

# Upper bound on a single NAV-range query (one year, leap years included). One
# row per day, so 366 `get` round trips is the worst case — beyond that the
# client splits the range itself.
MAX_NAV_RANGE_DAYS = 366


class PositionNotFoundError(Exception):
    """`position_key` does not exist or is owned by another tenant — not
    distinguished (no existence disclosure)."""


class PositionAccountNotFoundError(Exception):
    """`account_id` does not exist or is owned by another tenant — not
    distinguished (no existence disclosure)."""


class NavRangeInvalidError(ValueError):
    """`end_date < start_date`, or the range exceeds `MAX_NAV_RANGE_DAYS`."""


async def _owned_account_ids(
    conn: asyncpg.Connection, tenant_id: UUID, account_id: UUID | None
) -> list[UUID]:
    """List of `pos_account` ids owned by the tenant. If `account_id` is given,
    only that one (an empty list if not owned — the caller translates this to
    not-found)."""
    if account_id is None:
        rows = await conn.fetch(
            "SELECT account_id FROM pos_account WHERE tenant_id = $1 ORDER BY created_at",
            tenant_id,
        )
    else:
        rows = await conn.fetch(
            "SELECT account_id FROM pos_account WHERE tenant_id = $1 AND account_id = $2",
            tenant_id,
            account_id,
        )
    return [row["account_id"] for row in rows]


async def list_open_positions(
    pool: asyncpg.Pool, tenant_id: UUID, account_id: UUID, *, snapshots: SnapshotRepository
) -> list[PositionSnapshotView]:
    """All open positions with `quantity != 0`. An empty list if none
    (unchanged `SnapshotRepository.list_open` contract)."""
    async with pool.acquire() as conn:
        return await snapshots.list_open(conn, tenant_id, account_id)


async def list_positions(
    pool: asyncpg.Pool,
    tenant_id: UUID,
    *,
    account_id: UUID | None,
    instrument_id: UUID | None,
    snapshots: SnapshotRepository,
    portfolio_id: UUID | None = None,
    entities: EntityRepository | None = None,
) -> list[PositionSnapshotView]:
    """LB-19 `GET /positions` — the tenant's open positions (account /
    instrument / FA-6 portfolio filters). If `account_id` is not owned by
    the tenant, raises `PositionAccountNotFoundError`; a filter result with
    no matches is not an error, just an empty list.

    FA-6: if `portfolio_id` is omitted (existing callers), this function
    behaves byte-identically to before this leaf — all new parameters have
    defaults, and the new code path runs only when `portfolio_id is not
    None`. When it is given, it first fail-closed-verifies, via
    `resolve_portfolio_scope` (reusing FA-5's single entry point), that the
    portfolio is owned by this tenant and open (rejecting rather than
    falling back to returning everything, to block cross-portfolio leakage),
    then narrows the results by the `portfolio_id` embedded in
    `position_key` (FA-0d `PositionKey`) — this field has no separate
    column in the view contract (`PositionSnapshotView`), so it is parsed
    fresh. Old (pre-migration) 4-part keys cannot be attributed to any
    portfolio, so they are excluded whenever a scope filter is applied (no
    guessing values)."""
    if portfolio_id is not None:
        if entities is None:
            raise EntityContextResolutionError(
                "portfolio_id 스코프에는 entities 저장소가 필요합니다(정적 검사 우회 방어)."
            )
        await resolve_portfolio_scope(entities, tenant_id, portfolio_id)
    async with pool.acquire() as conn:
        accounts = await _owned_account_ids(conn, tenant_id, account_id)
        if account_id is not None and not accounts:
            raise PositionAccountNotFoundError(f"계정을 찾을 수 없습니다: {account_id}")
        views: list[PositionSnapshotView] = []
        for owned in accounts:
            views.extend(await snapshots.list_open(conn, tenant_id, owned))
    if instrument_id is not None:
        views = [view for view in views if view.instrument_id == instrument_id]
    if portfolio_id is not None:
        views = [view for view in views if _position_key_portfolio_id(view) == portfolio_id]
    return sorted(views, key=lambda view: view.position_key)


def _position_key_portfolio_id(view: PositionSnapshotView) -> UUID | None:
    try:
        return PositionKey.parse(view.position_key).portfolio_id
    except InvalidPositionKeyError:
        return None


async def list_journal(
    pool: asyncpg.Pool,
    tenant_id: UUID,
    position_key: str,
    *,
    after_seq: int,
    limit: int,
    snapshots: SnapshotRepository,
    journal: PositionJournalRepository,
) -> tuple[list[PositionJournalEntryView], int | None]:
    """LB-19 `GET /positions/{key}/journal` — up to `limit` entries starting
    from `sequence_no > after_seq`, plus the next cursor (last `sequence_no`)
    if there are more, or None otherwise. Snapshot ownership is verified
    first (the journal port does not know about tenants)."""
    if limit < 1:
        raise ValueError("limit는 1 이상이어야 합니다.")
    async with pool.acquire() as conn:
        if await snapshots.get(conn, tenant_id, position_key) is None:
            raise PositionNotFoundError(f"포지션을 찾을 수 없습니다: {position_key}")
        entries = await journal.list_for(conn, position_key, from_seq=after_seq)
    page = entries[:limit]
    next_seq = page[-1].sequence_no if len(entries) > limit else None
    return page, next_seq


async def get_nav(
    pool: asyncpg.Pool, account_id: UUID, nav_date: date, *, nav_repo: NavRepository
) -> NAVSnapshot | None:
    """NAV for the given date. `None` if not yet computed (unchanged
    `NavRepository.get` contract)."""
    async with pool.acquire() as conn:
        return await nav_repo.get(conn, account_id, nav_date)


async def list_nav_range(
    pool: asyncpg.Pool,
    tenant_id: UUID,
    account_id: UUID,
    *,
    start_date: date,
    end_date: date,
    nav_repo: NavRepository,
) -> list[NAVSnapshot]:
    """LB-19 `GET /positions/nav` — the daily NAV chain over
    `[start_date, end_date]`, ascending. A day not yet computed has no row
    (not zero-filled — the caller sees the missing date as-is)."""
    if end_date < start_date:
        raise NavRangeInvalidError("end_date가 start_date보다 앞섭니다.")
    if (end_date - start_date).days + 1 > MAX_NAV_RANGE_DAYS:
        raise NavRangeInvalidError(f"조회 범위는 최대 {MAX_NAV_RANGE_DAYS}일입니다.")
    async with pool.acquire() as conn:
        if not await _owned_account_ids(conn, tenant_id, account_id):
            raise PositionAccountNotFoundError(f"계정을 찾을 수 없습니다: {account_id}")
        series: list[NAVSnapshot] = []
        day = start_date
        while day <= end_date:
            nav = await nav_repo.get(conn, account_id, day)
            if nav is not None:
                series.append(nav)
            day += timedelta(days=1)
    return series
