"""FA-12 -- application/abor_snapshot.py: ABOR (accounting book of record) close marker.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-12
(task-2059 decision, PM 2026-09-08).

ABOR is "what the books said as of the close" -- once closed, the snapshot
value never changes (§1, decision item 5). `close_period` computes that
value exactly once via `ibor_view.compute_ibor_view` (recompute, no new
IBOR logic here) and persists it to `ledger_abor_snapshot`
(migration 8425d20c192e), a WORM table (`worm_sql`, L0-3) keyed
`UNIQUE (fund_id, as_of_date)`.

DoD(3) "reject re-close": a second `close_period` call for the same
`(fund_id, as_of_date)` hits that UNIQUE constraint. This module does not
let the raw `asyncpg.UniqueViolationError` propagate -- it maps it to
`AlreadyClosedError`, the same pattern `services/auth_service.py` uses for
the concurrent-signup 409 (task-2091): check `constraint_name` so an
unrelated UNIQUE violation is not misclassified, and never swallow it.

The snapshot hash reuses the exact construction `domain/hash_chain.lines_digest`
already uses (`hashlib.sha256` over `canonical_json`, LC-3) -- no new
hashing scheme, per decision item 3.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

import asyncpg
from pydantic import AwareDatetime, BaseModel

from src.foundation.ledger.application import ibor_view
from src.foundation.ledger.domain.hash_chain import canonical_json

_UNIQUE_CONSTRAINT = "ledger_abor_snapshot_fund_id_as_of_date_key"


class AlreadyClosedError(ValueError):
    """DoD(3) -- `(fund_id, as_of_date)`가 이미 마감되어 재마감을 거부한다."""

    def __init__(self, fund_id: UUID, as_of_date: date) -> None:
        super().__init__(
            f"fund_id={fund_id} as_of_date={as_of_date}: 이미 마감된 ABOR 기간입니다 "
            "(재마감 거부)."
        )
        self.fund_id = fund_id
        self.as_of_date = as_of_date


class SnapshotNotFoundError(LookupError):
    """`(fund_id, as_of_date)`에 대한 마감 스냅샷이 아직 없다."""

    def __init__(self, fund_id: UUID, as_of_date: date) -> None:
        super().__init__(f"fund_id={fund_id} as_of_date={as_of_date}: 마감 스냅샷이 없습니다.")
        self.fund_id = fund_id
        self.as_of_date = as_of_date


class AborSnapshotView(BaseModel):
    """영속화된 마감 스냅샷 하나 -- 마감 이후 이 값 자체는 절대 바뀌지 않는다.
    새 postings에 대한 신호는 `is_correction_pending`이 별도로 낸다(이
    뷰에는 담지 않는다 -- 스냅샷 값과 "그 후 무슨 일이 있었는지"를 섞지
    않기 위해)."""

    fund_id: UUID
    as_of_date: date
    balances: dict[str, Decimal]
    closing_recorded_at: AwareDatetime
    snapshot_hash: str


def _snapshot_hash(balances: dict[str, Decimal]) -> str:
    return hashlib.sha256(canonical_json(balances).encode("utf-8")).hexdigest()


def _row_to_view(row: asyncpg.Record) -> AborSnapshotView:
    raw_balances = json.loads(row["balances"])
    return AborSnapshotView(
        fund_id=row["fund_id"],
        as_of_date=row["as_of_date"],
        balances={code: Decimal(amount) for code, amount in raw_balances.items()},
        closing_recorded_at=row["closing_recorded_at"],
        snapshot_hash=row["snapshot_hash"],
    )


async def close_period(
    pool: asyncpg.Pool, *, fund_id: UUID, as_of_date: date, closing_recorded_at: datetime
) -> AborSnapshotView:
    """`fund_id`의 `as_of_date`를 `closing_recorded_at` cutoff로 마감한다.

    같은 `(fund_id, as_of_date)`로 다시 호출하면(DoD 3) `AlreadyClosedError`
    -- 스냅샷은 insert-only이고, "다시 마감해서 값을 고친다"는 개념 자체가
    없다(정정은 IBOR 쪽 표시일 뿐 ABOR 재작성이 아니다, decision item 5).
    """
    ibor = await ibor_view.compute_ibor_view(pool, fund_id=fund_id, cutoff=closing_recorded_at)
    balances_json = canonical_json(ibor.balances)
    snapshot_hash = _snapshot_hash(ibor.balances)

    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO ledger_abor_snapshot "
                "(fund_id, as_of_date, balances, closing_recorded_at, snapshot_hash) "
                "VALUES ($1, $2, $3::jsonb, $4, $5) "
                "RETURNING fund_id, as_of_date, balances, closing_recorded_at, snapshot_hash",
                fund_id,
                as_of_date,
                balances_json,
                closing_recorded_at,
                snapshot_hash,
            )
            return _row_to_view(row)
    except asyncpg.UniqueViolationError as exc:
        if exc.constraint_name != _UNIQUE_CONSTRAINT:
            raise
        raise AlreadyClosedError(fund_id, as_of_date) from exc


async def get_snapshot(
    pool: asyncpg.Pool, *, fund_id: UUID, as_of_date: date
) -> AborSnapshotView | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT fund_id, as_of_date, balances, closing_recorded_at, snapshot_hash "
            "FROM ledger_abor_snapshot WHERE fund_id = $1 AND as_of_date = $2",
            fund_id,
            as_of_date,
        )
    return _row_to_view(row) if row is not None else None


async def is_correction_pending(
    pool: asyncpg.Pool, *, fund_id: UUID, as_of_date: date
) -> bool:
    """DoD -- 마감된 `as_of_date`에 대해, 그 마감의 `closing_recorded_at`
    이후 새로 기록된 posting이 있으면 `True`(§9 FA-12, decision item 5).
    실제 판정은 `ibor_view.correction_pending`(WORM 저널 재조회)에 위임한다
    -- 이 함수는 그 cutoff를 스냅샷에서 읽어오는 것만 담당한다."""
    snapshot = await get_snapshot(pool, fund_id=fund_id, as_of_date=as_of_date)
    if snapshot is None:
        raise SnapshotNotFoundError(fund_id, as_of_date)
    return await ibor_view.correction_pending(
        pool, fund_id=fund_id, closing_recorded_at=snapshot.closing_recorded_at
    )
