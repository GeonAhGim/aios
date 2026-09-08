"""RD-20 — asyncpg implementation of `CorporateActionFilingRepository`.

Spec: docs/specs/L4_research_data_and_market_ecosystem_v1.0.md §9 RD-20.
Migration table `md_corporate_action_filing` (append-only, `source_ref`
UNIQUE) is the store — a different table from `md_corporate_action`
(LA-12, `(instrument_id, action_type, ex_date)` UNIQUE): LA-12 is the
ledger-integration table that manages "the single currently valid
value," while this table is the RD-20-only table that appends "the
full filing history, including corrections."
"""
from __future__ import annotations

from uuid import UUID

import asyncpg

from src.foundation.market_data.contracts.v1 import CorporateAction

__all__ = ["PostgresCorporateActionFilingRepository"]


def _row_to_action(row: asyncpg.Record) -> CorporateAction:
    return CorporateAction(
        action_type=row["action_type"],
        instrument_id=row["instrument_id"],
        ex_date=row["ex_date"],
        ratio=row["ratio"],
        cash_amount=row["cash_amount"],
        source_ref=row["source_ref"],
        known_at=row["known_at"],
    )


class PostgresCorporateActionFilingRepository:
    async def append(
        self, conn: asyncpg.Connection, action: CorporateAction
    ) -> CorporateAction:
        if action.known_at is None:
            raise ValueError("known_at 없는 CorporateAction은 RD-20 저장소에 넣을 수 없다")

        existing = await conn.fetchrow(
            "SELECT * FROM md_corporate_action_filing WHERE source_ref = $1",
            action.source_ref,
        )
        if existing is not None:
            return _row_to_action(existing)

        row = await conn.fetchrow(
            "INSERT INTO md_corporate_action_filing "
            "(instrument_id, action_type, ex_date, ratio, cash_amount, source_ref, known_at) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7) RETURNING *",
            action.instrument_id,
            action.action_type,
            action.ex_date,
            action.ratio,
            action.cash_amount,
            action.source_ref,
            action.known_at,
        )
        return _row_to_action(row)

    async def list_history(
        self, conn: asyncpg.Connection, instrument_id: UUID
    ) -> list[CorporateAction]:
        rows = await conn.fetch(
            "SELECT * FROM md_corporate_action_filing WHERE instrument_id = $1 "
            "ORDER BY known_at ASC",
            instrument_id,
        )
        return [_row_to_action(row) for row in rows]
