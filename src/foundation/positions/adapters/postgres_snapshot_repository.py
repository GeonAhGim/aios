"""LB-9 — `SnapshotRepository`(ports/snapshot_repository.py)의 asyncpg 구현.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§4.3, §5, §9 LB-8/LB-9.

`pos_snapshot`에는 currency 컬럼이 없다 — `avg_cost`/`mark_price`(Money)의
통화는 `pos_account.base_currency`를 그대로 쓴다. `get`/`list_open`은
`pos_account`를 조인해 그 값을 읽지만, `upsert`는 입력 `PositionSnapshotView.
base_currency`를 호출자가 이미 알고 있으므로 조인 없이 그 값을 그대로
재사용한다(쓰기 경로에서 불필요한 조회 한 번을 아낀다).

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
`existing` just read -- there is no race window between two concurrent
writers (one SQL round trip)."""
from __future__ import annotations

import json
from uuid import UUID

import asyncpg

from src.core.db.conditional_write import ConcurrencyConflictError
from src.data.models.base import Currency, Money
from src.foundation.positions.contracts.v1 import CostMethod, Lot, PositionSnapshotView

_SELECT = (
    "SELECT ps.*, pa.base_currency FROM pos_snapshot ps "
    "JOIN pos_account pa ON pa.account_id = ps.account_id "
)

_UPSERT_SQL = (
    "WITH existing AS MATERIALIZED ("
    " SELECT legacy_position_id FROM pos_snapshot WHERE position_key = $1"
    "), prior AS ("
    " DELETE FROM pos_snapshot"
    " WHERE position_key = $1 AND last_journal_seq IS NOT DISTINCT FROM $16"
    " RETURNING legacy_position_id"
    ") "
    "INSERT INTO pos_snapshot ("
    " position_key, tenant_id, account_id, instrument_id, quantity, avg_cost,"
    " cost_method, lots, realized_pnl_base, unrealized_pnl_base, fees_base,"
    " funding_base, mark_price, mark_at, last_journal_seq, legacy_position_id, updated_at"
    ") "
    "SELECT $1,$2,$3,$4,$5,$6,$7,$8::jsonb,$9,$10,$11,$12,$13,$14,$15,"
    " (SELECT legacy_position_id FROM prior), now() "
    "WHERE EXISTS (SELECT 1 FROM prior) OR NOT EXISTS (SELECT 1 FROM existing) "
    "RETURNING *"
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
        )
        if row is None:
            raise ConcurrencyConflictError(
                f"pos_snapshot.position_key={snapshot.position_key}: last_journal_seq가 "
                f"기대값({expected_seq})과 다릅니다(동시 갱신 충돌) — "
                "get으로 다시 조회 후 재시도하세요."
            )
        return _row_to_view(row, snapshot.base_currency)

    async def list_open(
        self, conn: asyncpg.Connection, tenant_id: UUID, account_id: UUID
    ) -> list[PositionSnapshotView]:
        rows = await conn.fetch(
            _SELECT + "WHERE ps.tenant_id = $1 AND ps.account_id = $2 AND ps.quantity != 0",
            tenant_id,
            account_id,
        )
        return [_row_to_view(row, Currency(row["base_currency"])) for row in rows]
