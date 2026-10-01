"""LB-15 — End-of-session daily NAV computation, chain verification, and
storage (application/compute_daily_nav.py).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.3, §9.3 LB-15.

This function only calls LB-6 `domain/nav.compute_daily_nav`/`verify_chain`
and does not reimplement the NAV formula (task-714 DoD 1). All this leaf
actually does is assemble inputs and delegate storage.

The balance-sheet side (`cash`/`position_mvs`) is populated directly by this
function from live sources — `cash` comes from `CashSource`, and position
mark-to-market uses the `mark_price` returned by `SnapshotRepository.
list_open` (already populated by LB-14 `mark_positions`), converted to the
base currency via `fx`. This function does not call `MarkPriceSource` again
(it is not in the signature, per the §2.3 table) — task-714 DoD 2 ("marks and
FX rates come only through the LB-14 source") already holds because this
cached value is only ever populated via the LB-14 path. If any open position
has `mark_price` of `None` (including stale, per task-654 decision), the
entire account's NAV computation is rejected — the rest is never backfilled
with estimates. The same rejection applies if `fx.rate` is missing or stale
(`domain/fx.FxRateMissingError`/`FxRateStaleError` propagate as-is).

The roll-forward side (`realized`/`unrealized_delta`/`funding`/`fees`/
`flows`) is received from `cmd` as already-computed daily values.
Unverified: the caller that aggregates these from the journal (scheduler,
LB-17) is out of scope for this leaf and does not exist yet — left as a note
here so the next leaf doesn't invent it ad hoc, as happened in task-654.

The previous day's NAV (`opening_nav`) is determined via LA-3 `VenueCalendar`
to find the prior trading day, then looked up with `nav_repo.get` for that
date (DoD 5). If there is no prior-day row (the account's first NAV), a
"genesis" snapshot with `opening_nav=0` is constructed and passed through to
`verify_chain` as-is — only the first day takes a different code path.

Storage is delegated to `nav_repo.insert` (LB-9 `PostgresNavRepository`) —
the adapter already implements rerun idempotency via `(account_id,
nav_date)` UNIQUE + `source_hash` comparison, and rejects chain violations
(a different `source_hash`) (task-714 DoD 3). Separately, `verify_chain`
checks the roll-forward equality itself (is this computation internally
consistent) *before* storage (DoD 4) — this catches different failures than
the adapter's `source_hash` comparison (is this computation the same as the
previous one).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Protocol, runtime_checkable
from uuid import UUID

import asyncpg
from pydantic import AwareDatetime, BaseModel

from src.data.models.base import Currency, FXRate, Money
from src.foundation.market_data.api import VenueCalendar
from src.foundation.positions.contracts.v1 import NAVSnapshot, PositionSnapshotView
from src.foundation.positions.domain import fx as fx_calc
from src.foundation.positions.domain import nav
from src.foundation.positions.ports.fx_rate_source import FxRateSource
from src.foundation.positions.ports.nav_repository import NavRepository
from src.foundation.positions.ports.snapshot_repository import SnapshotRepository

__all__ = [
    "CashSource",
    "ComputeDailyNavCommand",
    "NavCashUnavailableError",
    "NavMarkUnavailableError",
    "compute_daily_nav",
]

_LOOKBACK_DAYS = 30


class NavCashUnavailableError(Exception):
    """If the `cash` source cannot provide a value (disconnected /
    uninitialized), reject NAV computation outright instead of
    substituting `0`."""

    def __init__(self, account_id: UUID) -> None:
        super().__init__(f"{account_id}: cannot fetch cash balance — NAV computation rejected")
        self.account_id = account_id


class NavMarkUnavailableError(Exception):
    """If any open position lacks a `mark_price` (including stale), reject
    NAV computation for the entire account because of that single position
    (no estimate substitution, task-714 DoD 2)."""

    def __init__(self, position_key: str) -> None:
        super().__init__(
            f"{position_key}: mark_price is missing (stale/not received) — NAV computation rejected"
        )
        self.position_key = position_key


@runtime_checkable
class CashSource(Protocol):
    """The account's cash balance denominated in the base currency. Returns
    `None` instead of substituting `0` when the value is unavailable — the
    same contract as other LB-14-family ports. Unverified: no real adapter
    (wrapping an exchange/ledger balance) exists yet (out of scope for this
    leaf)."""

    async def cash(self, account_id: UUID, at: AwareDatetime) -> Decimal | None: ...


class ComputeDailyNavCommand(BaseModel):
    """Input for `compute_daily_nav`. `cash`/`position_mvs` are not part of
    the command because this function populates them directly from live
    sources — only the roll-forward side (`realized` through `flows`) is
    received as already-computed daily values (see module docstring)."""

    tenant_id: UUID
    account_id: UUID
    base_currency: Currency
    at: AwareDatetime
    realized: Decimal
    unrealized_delta: Decimal
    funding: Decimal
    fees: Decimal
    flows: Decimal
    trace_id: UUID


def _previous_trading_day(calendar: VenueCalendar, day: date) -> date | None:
    for offset in range(1, _LOOKBACK_DAYS + 1):
        candidate = day - timedelta(days=offset)
        if calendar.sessions_for(candidate):
            return candidate
    return None


def _genesis(account_id: UUID, nav_date: date, base_currency: Currency) -> NAVSnapshot:
    """Placeholder passed to `verify_chain` when there is no prior-day NAV
    row (the account's first NAV) — with `closing_nav=0`, the continuity
    check (`cur.opening_nav==prev.closing_nav`) lines up exactly with a
    first day whose `opening_nav=0`."""

    zero = Decimal("0")
    return NAVSnapshot(
        account_id=account_id,
        nav_date=nav_date - timedelta(days=1),
        base_currency=base_currency,
        opening_nav=zero,
        cash=zero,
        positions_mv=zero,
        realized=zero,
        unrealized_delta=zero,
        funding=zero,
        fees=zero,
        flows=zero,
        closing_nav=zero,
        fx_rates=[],
        source_hash="genesis",
    )


@dataclass(frozen=True, slots=True)
class _PositionsMv:
    values: list[Decimal]
    fx_rates: list[FXRate]


async def _positions_mv(
    open_positions: list[PositionSnapshotView],
    base_currency: Currency,
    at: AwareDatetime,
    *,
    fx: FxRateSource,
) -> _PositionsMv:
    values: list[Decimal] = []
    fx_rates: list[FXRate] = []
    for snapshot in open_positions:
        if snapshot.mark_price is None:
            raise NavMarkUnavailableError(snapshot.position_key)
        gross = Money(
            amount=snapshot.quantity * snapshot.mark_price.amount,
            currency=snapshot.mark_price.currency,
        )
        rate: FXRate | None = None
        if gross.currency != base_currency:
            rate = await fx.rate(gross.currency, base_currency, at)
        converted = fx_calc.convert(gross, base_currency, rate, now=at)
        if converted.rate is not None:
            fx_rates.append(converted.rate)
        values.append(converted.amount)
    return _PositionsMv(values=values, fx_rates=fx_rates)


async def compute_daily_nav(
    cmd: ComputeDailyNavCommand,
    *,
    snapshots: SnapshotRepository,
    cash: CashSource,
    nav_repo: NavRepository,
    calendar: VenueCalendar,
    fx: FxRateSource,
    pool: asyncpg.Pool,
) -> NAVSnapshot:
    nav_date = calendar.trading_day_of(cmd.at)

    async with pool.acquire() as conn:
        open_positions = await snapshots.list_open(conn, cmd.tenant_id, cmd.account_id)
        prev_day = _previous_trading_day(calendar, nav_date)
        prev_nav = await nav_repo.get(conn, cmd.account_id, prev_day) if prev_day else None

    cash_balance = await cash.cash(cmd.account_id, cmd.at)
    if cash_balance is None:
        raise NavCashUnavailableError(cmd.account_id)

    mv = await _positions_mv(open_positions, cmd.base_currency, cmd.at, fx=fx)

    opening_nav = prev_nav.closing_nav if prev_nav is not None else Decimal("0")
    inputs = nav.NavInputs(
        account_id=cmd.account_id,
        nav_date=nav_date,
        base_currency=cmd.base_currency,
        opening_nav=opening_nav,
        cash=cash_balance,
        position_mvs=mv.values,
        realized=cmd.realized,
        unrealized_delta=cmd.unrealized_delta,
        funding=cmd.funding,
        fees=cmd.fees,
        flows=cmd.flows,
        fx_rates=mv.fx_rates,
    )
    candidate = nav.compute_daily_nav(inputs)
    nav.verify_chain(
        prev_nav if prev_nav is not None else _genesis(cmd.account_id, nav_date, cmd.base_currency),
        candidate,
    )

    async with pool.acquire() as conn:
        return await nav_repo.insert(conn, candidate)
