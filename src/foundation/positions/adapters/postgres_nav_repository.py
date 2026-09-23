"""LB-9 — asyncpg implementation of `NavRepository` (ports/nav_repository.py).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§4.3, §5, §9 LB-8/LB-9.

`pos_nav_daily` is WORM (§9 LB-8 migration), so only `insert` is allowed. Per §5 table,
attempts `INSERT ... ON CONFLICT (account_id, nav_date) DO NOTHING RETURNING`; on
conflict (row for that date already exists), re-reads the existing row and compares
`source_hash` — a mismatch means the recomputed result differs from the previous one, so
raises `NavChainBrokenError` (POS_NAV_CHAIN_BROKEN, no overwrite, requires ops intervention).
If equal, it is a retry of the same recomputation, so returns the existing row as-is
(port docstring: caller should check idempotency via `get` first, but if a retried
call skips that check and data is identical, we silently accept it here). Other
constraint violations such as `CHECK(closing_nav = cash + positions_mv)` propagate as
raw asyncpg exceptions (fail-closed, no custom wrapping)."""
from __future__ import annotations

import json
from datetime import date
from uuid import UUID

import asyncpg

from src.data.models.base import Currency, FXRate
from src.foundation.positions.contracts.v1 import NAVSnapshot

_INSERT_SQL = (
    "INSERT INTO pos_nav_daily ("
    " account_id, nav_date, base_currency, opening_nav, cash, positions_mv,"
    " realized, unrealized_delta, funding, fees, flows, closing_nav, fx_rates, source_hash"
    ") VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13::jsonb,$14) "
    "ON CONFLICT (account_id, nav_date) DO NOTHING "
    "RETURNING *"
)


class NavChainBrokenError(Exception):
    """POS_NAV_CHAIN_BROKEN — the same `(account_id, nav_date)` was recomputed with a
    different `source_hash`. No overwrite allowed, no retry (operations intervention required)."""

    def __init__(self, account_id: UUID, nav_date: date) -> None:
        super().__init__(
            f"pos_nav_daily UNIQUE(account_id={account_id}, nav_date={nav_date}) 충돌: "
            "source_hash가 기존 값과 다릅니다(덮어쓰기 금지)."
        )
        self.account_id = account_id
        self.nav_date = nav_date


def _fx_rates_to_json(rates: list[FXRate]) -> str:
    return json.dumps([rate.model_dump(mode="json") for rate in rates])


def _fx_rates_from_json(raw: str) -> list[FXRate]:
    return [FXRate.model_validate(item) for item in json.loads(raw)]


def _row_to_view(row: asyncpg.Record) -> NAVSnapshot:
    return NAVSnapshot(
        account_id=row["account_id"],
        nav_date=row["nav_date"],
        base_currency=Currency(row["base_currency"]),
        opening_nav=row["opening_nav"],
        cash=row["cash"],
        positions_mv=row["positions_mv"],
        realized=row["realized"],
        unrealized_delta=row["unrealized_delta"],
        funding=row["funding"],
        fees=row["fees"],
        flows=row["flows"],
        closing_nav=row["closing_nav"],
        fx_rates=_fx_rates_from_json(row["fx_rates"]),
        source_hash=row["source_hash"],
    )


class PostgresNavRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def insert(self, conn: asyncpg.Connection, nav: NAVSnapshot) -> NAVSnapshot:
        row = await conn.fetchrow(
            _INSERT_SQL,
            nav.account_id,
            nav.nav_date,
            nav.base_currency.value,
            nav.opening_nav,
            nav.cash,
            nav.positions_mv,
            nav.realized,
            nav.unrealized_delta,
            nav.funding,
            nav.fees,
            nav.flows,
            nav.closing_nav,
            _fx_rates_to_json(nav.fx_rates),
            nav.source_hash,
        )
        if row is not None:
            return _row_to_view(row)

        existing = await self.get(conn, nav.account_id, nav.nav_date)
        if existing is None:
            raise RuntimeError(
                f"pos_nav_daily UNIQUE 충돌 후 재조회 실패: account_id={nav.account_id} "
                f"nav_date={nav.nav_date}"
            )
        if existing.source_hash != nav.source_hash:
            raise NavChainBrokenError(nav.account_id, nav.nav_date)
        return existing

    async def get(
        self, conn: asyncpg.Connection, account_id: UUID, nav_date: date
    ) -> NAVSnapshot | None:
        row = await conn.fetchrow(
            "SELECT * FROM pos_nav_daily WHERE account_id = $1 AND nav_date = $2",
            account_id,
            nav_date,
        )
        return None if row is None else _row_to_view(row)
