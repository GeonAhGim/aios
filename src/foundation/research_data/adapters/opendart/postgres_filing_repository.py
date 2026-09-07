"""RD-20 — `CorporateActionFilingRepository`의 asyncpg 구현.

Spec: docs/specs/L4_research_data_and_market_ecosystem_v1.0.md §9 RD-20.
마이그레이션 `md_corporate_action_filing`(append-only, `source_ref` UNIQUE)이
저장소다 — `md_corporate_action`(LA-12, `(instrument_id, action_type,
ex_date)` UNIQUE)과는 다른 테이블이다: LA-12는 "지금 유효한 값 하나"를
관리하는 장부 연동 테이블이고, 이 테이블은 "정정을 포함한 전체 공시
이력"을 append-only로 쌓는 RD-20 전용 테이블이다.
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
