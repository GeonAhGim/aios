"""U-2a — `GET /v1/accounts/summary` response contract v1.

Spec: docs/specs/L4_product_experience_and_discovery_v1.0.md#U-2,
ADR-2026-09-09-B Decision C.

This leaf narrows scope (task-2629 decision, after a 3rd attempt blew the
turn budget): it aggregates only per-account open positions (LB-17
`list_open_positions`) and cash (LB-7/9 `pos_nav_daily` latest row).
Exposure and multi-fund/portfolio scoping are deferred to a follow-up leaf.

`cash`/`positions_mv`/`closing_nav` are `None` when EOD `compute_daily_nav`
(LB-15) has never run for that account yet — substituting 0 would make
"not yet settled" indistinguishable from "balance is actually zero" (same
principle as the `MARK_STALE` family in §3.2, LB-1 module docstring).
`totals` sums only within the same currency — converting different
currencies (KRW/USDT etc.) into one is an "exposure" aggregation that needs
a live FX lookup, which is out of scope for this leaf."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

from src.data.models.base import Currency
from src.foundation.positions.contracts.v1 import PositionSnapshotView

SCHEMA_VERSION: Literal["v1"] = "v1"


class AccountCashSummary(BaseModel):
    """Cash / position market value / unrealized aggregation for one account."""

    account_id: UUID
    venue: str
    base_currency: Currency
    nav_date: date | None
    cash: Decimal | None
    positions_mv: Decimal | None
    closing_nav: Decimal | None
    open_positions: list[PositionSnapshotView]
    schema_version: Literal["v1"] = SCHEMA_VERSION


class CurrencyTotal(BaseModel):
    """Account totals grouped by one currency (exact Decimal sum, same-currency only)."""

    base_currency: Currency
    cash: Decimal
    positions_mv: Decimal
    closing_nav: Decimal
    schema_version: Literal["v1"] = SCHEMA_VERSION


class AccountsSummaryResponse(BaseModel):
    accounts: list[AccountCashSummary]
    totals: list[CurrencyTotal]
    schema_version: Literal["v1"] = SCHEMA_VERSION


__all__ = [
    "SCHEMA_VERSION",
    "AccountCashSummary",
    "AccountsSummaryResponse",
    "CurrencyTotal",
]
