"""U-2a — read-only application layer for `GET /v1/accounts/summary`.

Spec: docs/specs/L4_product_experience_and_discovery_v1.0.md#U-2,
ADR-2026-09-09-B Decision C.

Read-only: delegates to `pos_account`/`pos_snapshot`/`pos_nav_daily` without
writing anything (same principle as LB-17, §6). Reusing FA-6
`resolve_portfolio_scope` is out of scope for this leaf (task-2629
decision) — account ownership is confirmed only via `pos_account.tenant_id`
(same pattern as LB-17's `_owned_account_ids`; that function is private, so
this module keeps its own instead of reusing it).

Raises `AccountNotFoundError` when `account_id` is given but not owned by
this tenant (nonexistent and other-tenant-owned are not distinguished —
no existence disclosure). This is a separate exception class from LB-17's
`PositionAccountNotFoundError` — reporting does not borrow positions'
internal exception taxonomy (§4 contract-ownership principle)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

import asyncpg

from src.data.models.base import Currency
from src.foundation.positions.ports.snapshot_repository import SnapshotRepository
from src.foundation.reporting.contracts.v1 import (
    AccountCashSummary,
    AccountsSummaryResponse,
    CurrencyTotal,
)

__all__ = ["AccountNotFoundError", "get_accounts_summary"]


class AccountNotFoundError(Exception):
    """`account_id` does not exist or belongs to another tenant — not
    distinguished (no existence disclosure)."""


async def _owned_accounts(
    conn: asyncpg.Connection, tenant_id: UUID, account_id: UUID | None
) -> list[tuple[UUID, str, Currency]]:
    if account_id is None:
        rows = await conn.fetch(
            "SELECT account_id, venue, base_currency FROM pos_account "
            "WHERE tenant_id = $1 ORDER BY created_at",
            tenant_id,
        )
    else:
        rows = await conn.fetch(
            "SELECT account_id, venue, base_currency FROM pos_account "
            "WHERE tenant_id = $1 AND account_id = $2",
            tenant_id,
            account_id,
        )
    return [(row["account_id"], row["venue"], Currency(row["base_currency"])) for row in rows]


async def _latest_nav(
    conn: asyncpg.Connection, account_id: UUID
) -> tuple[Decimal, Decimal, Decimal, date] | None:
    row = await conn.fetchrow(
        "SELECT cash, positions_mv, closing_nav, nav_date FROM pos_nav_daily "
        "WHERE account_id = $1 ORDER BY nav_date DESC LIMIT 1",
        account_id,
    )
    if row is None:
        return None
    return row["cash"], row["positions_mv"], row["closing_nav"], row["nav_date"]


def _add_totals(
    totals: dict[Currency, CurrencyTotal],
    currency: Currency,
    cash: Decimal,
    mv: Decimal,
    nav: Decimal,
) -> None:
    existing = totals.get(currency)
    if existing is None:
        totals[currency] = CurrencyTotal(
            base_currency=currency, cash=cash, positions_mv=mv, closing_nav=nav
        )
        return
    totals[currency] = CurrencyTotal(
        base_currency=currency,
        cash=existing.cash + cash,
        positions_mv=existing.positions_mv + mv,
        closing_nav=existing.closing_nav + nav,
    )


async def get_accounts_summary(
    pool: asyncpg.Pool,
    tenant_id: UUID,
    *,
    account_id: UUID | None,
    snapshots: SnapshotRepository,
) -> AccountsSummaryResponse:
    """U-2a `GET /accounts/summary` — returns the tenant's owned account(s)'
    open positions (LB-17 `list_open`) and latest EOD cash snapshot (LB-9
    `pos_nav_daily` latest row), per account and grouped into per-currency
    totals. If `account_id` is given, only that one account (raises
    `AccountNotFoundError` if not owned); otherwise all of the tenant's
    accounts."""
    async with pool.acquire() as conn:
        owned = await _owned_accounts(conn, tenant_id, account_id)
        if account_id is not None and not owned:
            raise AccountNotFoundError(f"account not found: {account_id}")

        accounts: list[AccountCashSummary] = []
        totals: dict[Currency, CurrencyTotal] = {}
        for owned_account_id, venue, currency in owned:
            open_positions = await snapshots.list_open(conn, tenant_id, owned_account_id)
            nav = await _latest_nav(conn, owned_account_id)
            if nav is None:
                accounts.append(
                    AccountCashSummary(
                        account_id=owned_account_id,
                        venue=venue,
                        base_currency=currency,
                        nav_date=None,
                        cash=None,
                        positions_mv=None,
                        closing_nav=None,
                        open_positions=open_positions,
                    )
                )
                continue
            cash, positions_mv, closing_nav, nav_date = nav
            accounts.append(
                AccountCashSummary(
                    account_id=owned_account_id,
                    venue=venue,
                    base_currency=currency,
                    nav_date=nav_date,
                    cash=cash,
                    positions_mv=positions_mv,
                    closing_nav=closing_nav,
                    open_positions=open_positions,
                )
            )
            _add_totals(totals, currency, cash, positions_mv, closing_nav)

    return AccountsSummaryResponse(
        accounts=accounts,
        totals=sorted(totals.values(), key=lambda t: t.base_currency.value),
    )
