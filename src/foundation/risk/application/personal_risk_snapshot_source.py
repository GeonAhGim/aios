"""task-6510 -- reads real position/P&L state (the `positions` bounded
context's daily NAV chain, `pos_nav_daily`) to compute the
personal-conservative layer's account-equity/daily-loss inputs for the
periodic monitor (`personal_daily_loss_monitor.py`).

Known limitation (unchanged from task-3986's own docstring in
`foundation_personal_gate.py`): `pos_nav_daily` is populated by a daily
batch job (`positions.application.scheduler.PositionsScheduler`) that
`main.py` does not wire any tracked accounts into yet (LA-18, a separate
leaf, task-712 decision) -- until that lands, this returns `None`
whenever no NAV row exists yet for the scoped tenant's accounts on the
given date, the same "nothing to evaluate this tick" no-op every other
caller-optional input in this module already uses (`observed_fence`,
`personal_risk_snapshot` itself) rather than guessing a value.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

import asyncpg

from src.foundation.positions.ports.nav_repository import NavRepository


@dataclass(frozen=True)
class PersonalDailyLossInput:
    account_equity_krw: Decimal
    daily_realized_pnl_pct: Decimal


async def compute_personal_daily_loss_input(
    pool: asyncpg.Pool, tenant_id: UUID, nav_date: date, *, nav_repo: NavRepository
) -> PersonalDailyLossInput | None:
    """Aggregates `nav_date`'s NAV row across every `pos_account` the
    tenant owns. `None` when the tenant owns no account, none of them has
    a NAV row for `nav_date` yet, or the aggregated `opening_nav` is not
    positive (division by zero / meaningless ratio -- not guessed as 0%)."""
    async with pool.acquire() as conn:
        account_ids = [
            row["account_id"]
            for row in await conn.fetch(
                "SELECT account_id FROM pos_account WHERE tenant_id = $1", tenant_id
            )
        ]
        if not account_ids:
            return None
        navs = []
        for account_id in account_ids:
            nav = await nav_repo.get(conn, account_id, nav_date)
            if nav is not None:
                navs.append(nav)
    if not navs:
        return None
    equity = sum((nav.closing_nav for nav in navs), Decimal("0"))
    opening = sum((nav.opening_nav for nav in navs), Decimal("0"))
    realized = sum((nav.realized for nav in navs), Decimal("0"))
    if opening <= 0:
        return None
    return PersonalDailyLossInput(
        account_equity_krw=equity,
        daily_realized_pnl_pct=realized / opening,
    )
