"""LB-9 -- asyncpg implementation of `SnapshotRepository` (ports/snapshot_repository.py).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§4.3, §5, §9 LB-8/LB-9.
FA-0d-fix: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-0d
(task-771991202, CTO decision (a) on task-2405).

`pos_snapshot` has no currency column -- the currency of `avg_cost`/`mark_price`
(Money) is `pos_account.base_currency`. `get`/`list_open` join `pos_account`
to read it; `upsert` reuses the caller's `PositionSnapshotView.base_currency`
without the join (saves one lookup on the write path).

`upsert` implements the same optimistic-locking semantics as §5's
`conditional_update(pos_snapshot, id=position_key, expected
last_journal_seq)`, but under the no-UPDATE trigger FA-10
(`docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-10`) put on
`pos_snapshot` -- `INSERT ... ON CONFLICT DO UPDATE` (an UPDATE under the
hood) is no longer available, so this does `existing` (pre-write state,
pinned via a MATERIALIZED CTE) -> `prior` (DELETE the old row only if its
seq matches, carrying `legacy_position_id` forward via RETURNING) -> INSERT
(the new version), all atomically in one statement. First creation
(`expected_seq=0`, per the port's docstring) hits the `NOT EXISTS(existing)`
branch and just inserts, since `existing` is empty; later writes only
insert a new row if `prior` deleted a row matching the same snapshot
`existing` just read.

Two concurrent first-creations (`expected_seq=0`) for the same brand-new
`position_key` both see `existing` empty and both attempt the INSERT --
without `ON CONFLICT (position_key) DO NOTHING`, the loser used to
surface a raw `asyncpg.UniqueViolationError` (reproduced empirically:
QA task-2095) instead of the domain-level `ConcurrencyConflictError` the
`row is None` branch below raises for every other conflict shape. `DO
NOTHING` folds that race into the same `row is None` path -- it never
fires for the "replace" case since `prior`'s DELETE has already removed
the old row (same natural key) before this INSERT runs, so there is
nothing left to conflict with.

FA-0d-fix (task-771991202, root cause of CI red 77871f67): the 5-part
`position_key` (FA-0d `PositionKey`) already carries `portfolio_id`, but this
adapter never wrote it into the `pos_snapshot.portfolio_id` column that FA-4
(`963d5f3cfb1b`) added -- the column stayed NULL for every adapter-written
row. The FA-0d backfill migration (`cdb114b6903f`) rewrites keys 5->4 parts
on downgrade and needs that column to rebuild the 5th part on upgrade, so
any migration round trip crossing it (OMS `test_db_transition_trigger`,
risk-gate rollback tests) fail-closed with `UnbackfillablePositionKeyError`
and left the database stuck below head, cascading into dozens of failures.
`upsert` now parses the key through the central constructor (rejecting
legacy/malformed keys up front with `InvalidPositionKeyError`), writes
`portfolio_id` plus the portfolio's `fund_id` into their columns, and fences
the write on portfolio ownership: the `owned_portfolio` CTE resolves the
portfolio through fund -> legal_entity -> `tenant_id`, and both the DELETE
and the INSERT are gated on it, so a cross-tenant (or never bootstrapped)
portfolio neither deletes nor inserts anything. The failure path then runs
one diagnostic lookup to raise the precise port error
(`SnapshotPortfolioNotFoundError` / `SnapshotPortfolioTenantMismatchError`)
instead of a misleading `ConcurrencyConflictError`; the happy path stays a
single round trip (the journal-append perf guard counts them).
"""

from __future__ import annotations

import json
from uuid import UUID

import asyncpg

from src.core.db.conditional_write import ConcurrencyConflictError
from src.data.models.base import Currency, Money
from src.foundation.positions.contracts.v1 import CostMethod, Lot, PositionSnapshotView
from src.foundation.positions.domain.position_key import PositionKey
from src.foundation.positions.ports.snapshot_repository import (
    SnapshotPortfolioNotFoundError,
    SnapshotPortfolioTenantMismatchError,
)

_SELECT = (
    "SELECT ps.*, pa.base_currency FROM pos_snapshot ps "
    "JOIN pos_account pa ON pa.account_id = ps.account_id "
)

_UPSERT_SQL = (
    "WITH owned_portfolio AS MATERIALIZED ("
    " SELECT p.portfolio_id, p.fund_id FROM portfolio p"
    " JOIN fund f ON f.fund_id = p.fund_id"
    " JOIN legal_entity le ON le.entity_id = f.entity_id"
    " WHERE p.portfolio_id = $17 AND le.tenant_id = $2"
    "), existing AS MATERIALIZED ("
    " SELECT legacy_position_id FROM pos_snapshot WHERE position_key = $1"
    "), prior AS ("
    " DELETE FROM pos_snapshot"
    " WHERE position_key = $1 AND tenant_id = $2"
    " AND last_journal_seq IS NOT DISTINCT FROM $16"
    " AND EXISTS (SELECT 1 FROM owned_portfolio)"
    " RETURNING legacy_position_id"
    ") "
    "INSERT INTO pos_snapshot ("
    " position_key, tenant_id, account_id, instrument_id, quantity, avg_cost,"
    " cost_method, lots, realized_pnl_base, unrealized_pnl_base, fees_base,"
    " funding_base, mark_price, mark_at, last_journal_seq, legacy_position_id,"
    " fund_id, portfolio_id, updated_at"
    ") "
    "SELECT $1,$2,$3,$4,$5,$6,$7,$8::jsonb,$9,$10,$11,$12,$13,$14,$15,"
    " (SELECT legacy_position_id FROM prior), op.fund_id, op.portfolio_id, now() "
    "FROM owned_portfolio op "
    "WHERE EXISTS (SELECT 1 FROM prior) OR NOT EXISTS (SELECT 1 FROM existing) "
    "ON CONFLICT (position_key) DO NOTHING "
    "RETURNING *"
)

# Failure-path diagnostic only (never on the happy path): who owns the portfolio?
_PORTFOLIO_OWNER_SQL = (
    "SELECT le.tenant_id FROM portfolio p"
    " JOIN fund f ON f.fund_id = p.fund_id"
    " JOIN legal_entity le ON le.entity_id = f.entity_id"
    " WHERE p.portfolio_id = $1"
)


def _lots_to_json(lots: list[Lot]) -> str:
    return json.dumps([lot.model_dump(mode="json") for lot in lots])


def _lots_from_json(raw: str) -> list[Lot]:
    return [Lot.model_validate(item) for item in json.loads(raw)]


def _row_to_view(row: asyncpg.Record, currency: Currency) -> PositionSnapshotView:
    mark_price = (
        None if row["mark_price"] is None else Money(amount=row["mark_price"], currency=currency)
    )
    return PositionSnapshotView(
        position_key=row["position_key"],
        tenant_id=row["tenant_id"],
        account_id=row["account_id"],
        instrument_id=row["instrument_id"],
        quantity=row["quantity"],
        avg_cost=Money(amount=row["avg_cost"], currency=currency),
        cost_method=CostMethod(row["cost_method"]),
        lots=_lots_from_json(row["lots"]),
        realized_pnl_base=row["realized_pnl_base"],
        unrealized_pnl_base=row["unrealized_pnl_base"],
        fees_base=row["fees_base"],
        funding_base=row["funding_base"],
        mark_price=mark_price,
        mark_at=row["mark_at"],
        base_currency=currency,
        last_journal_seq=row["last_journal_seq"],
        updated_at=row["updated_at"],
    )


class PostgresSnapshotRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def get(
        self, conn: asyncpg.Connection, tenant_id: UUID, position_key: str
    ) -> PositionSnapshotView | None:
        row = await conn.fetchrow(
            _SELECT + "WHERE ps.tenant_id = $1 AND ps.position_key = $2", tenant_id, position_key
        )
        return None if row is None else _row_to_view(row, Currency(row["base_currency"]))

    async def upsert(
        self, conn: asyncpg.Connection, snapshot: PositionSnapshotView, expected_seq: int
    ) -> PositionSnapshotView:
        # Central constructor is the only parser -- raises InvalidPositionKeyError
        # for legacy 4-part / malformed keys before anything touches the table.
        portfolio_id = PositionKey.parse(snapshot.position_key).portfolio_id
        row = await conn.fetchrow(
            _UPSERT_SQL,
            snapshot.position_key,
            snapshot.tenant_id,
            snapshot.account_id,
            snapshot.instrument_id,
            snapshot.quantity,
            snapshot.avg_cost.amount,
            snapshot.cost_method.value,
            _lots_to_json(snapshot.lots),
            snapshot.realized_pnl_base,
            snapshot.unrealized_pnl_base,
            snapshot.fees_base,
            snapshot.funding_base,
            None if snapshot.mark_price is None else snapshot.mark_price.amount,
            snapshot.mark_at,
            snapshot.last_journal_seq,
            expected_seq,
            portfolio_id,
        )
        if row is None:
            await self._raise_for_failed_upsert(conn, snapshot, portfolio_id, expected_seq)
        return _row_to_view(row, snapshot.base_currency)

    @staticmethod
    async def _raise_for_failed_upsert(
        conn: asyncpg.Connection,
        snapshot: PositionSnapshotView,
        portfolio_id: UUID,
        expected_seq: int,
    ) -> None:
        owner_tenant_id = await conn.fetchval(_PORTFOLIO_OWNER_SQL, portfolio_id)
        if owner_tenant_id is None:
            raise SnapshotPortfolioNotFoundError(
                f"pos_snapshot.position_key={snapshot.position_key}: portfolio "
                f"{portfolio_id} does not exist -- bootstrap the tenant's default "
                "hierarchy (ensure_default_hierarchy) before writing positions."
            )
        if owner_tenant_id != snapshot.tenant_id:
            raise SnapshotPortfolioTenantMismatchError(
                f"pos_snapshot.position_key={snapshot.position_key}: portfolio "
                f"{portfolio_id} is not owned by tenant {snapshot.tenant_id} -- "
                "cross-tenant snapshot write rejected."
            )
        raise ConcurrencyConflictError(
            f"pos_snapshot.position_key={snapshot.position_key}: last_journal_seq가 "
            f"기대값({expected_seq})과 다릅니다(동시 갱신 충돌) — "
            "get으로 다시 조회 후 재시도하세요."
        )

    async def list_open(
        self, conn: asyncpg.Connection, tenant_id: UUID, account_id: UUID
    ) -> list[PositionSnapshotView]:
        rows = await conn.fetch(
            _SELECT + "WHERE ps.tenant_id = $1 AND ps.account_id = $2 AND ps.quantity != 0",
            tenant_id,
            account_id,
        )
        return [_row_to_view(row, Currency(row["base_currency"])) for row in rows]
