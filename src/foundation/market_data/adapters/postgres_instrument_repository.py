"""DC-8 — `ports/instrument_repository.py`(DC-5)의 asyncpg 구현.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§2.1 DC-5·DC-8, §3.2(계약), §4.1(불변조건), §9.2 DC-8.

DC-4(dbaf260f2917) 마이그레이션이 만든 `instruments`/`venue_listings`
테이블 위에 `InstrumentRepository` Protocol을 그대로 구현한다 — 필드·
메서드 시그니처는 포트 정의를 재정의하지 않는다(task-1195 decision).

`instrument_id` 불변·`venue_listings` 기간 겹침 금지(§4.1)는 DB 제약
(트리거·`EXCLUDE USING gist`, DC-4)이 이미 강제하므로 이 어댑터는 그
예외(`CheckViolationError`/`ExclusionViolationError`)를 도메인 예외로
바꿔 던지기만 하고 앱 레벨 사전 검사를 다시 하지 않는다.

`update_lifecycle_state`는 "읽은 상태를 쓰기 조건으로 건다"(105번 동시성
표준 규칙 1)를 따른다 — DC-3(`domain/instruments/lifecycle.py`)이 현재
상태를 읽고 `transition()`으로 다음 상태를 계산한 뒤 이 메서드를 부르는
호출 순서라, 그 사이 다른 트랜잭션이 먼저 상태를 바꿨을 가능성을
`expected_state` WHERE 조건 없이 무시하면 안 된다(예: ACTIVE에서 동시에
HALT·DELIST 두 전이가 경합하면 나중 커밋이 조용히 이긴다). 조건 불일치는
공용 `ConcurrencyConflictError`(`src/core/db/conditional_write.py`)로,
instrument_id 자체가 없으면 `InstrumentNotFoundError`로 구분한다
(`postgres_balance_repository.apply`와 동일 패턴 — 실패 시 존재 여부를
별도 SELECT로 한 번 더 확인).
"""
from __future__ import annotations

import asyncpg
from pydantic import AwareDatetime

from src.core.db.conditional_write import ConcurrencyConflictError
from src.data.models.base import AssetClass
from src.foundation.market_data.contracts.v1 import Venue
from src.foundation.market_data.contracts.v2.instruments import (
    Instrument,
    InstrumentLifecycle,
    VenueListing,
)

__all__ = [
    "DuplicateInstrumentIdError",
    "InstrumentNotFoundError",
    "VenueListingOverlapError",
    "PostgresInstrumentRepository",
]


class DuplicateInstrumentIdError(Exception):
    """`create()`가 이미 존재하는 `instrument_id`로 불림 — §4.1
    `instrument_id` 불변, 이 메서드에 UPDATE 경로는 없다(instruments PK
    위반을 그대로 노출하지 않고 도메인 예외로 감싼다)."""


class InstrumentNotFoundError(Exception):
    """`update_lifecycle_state()`가 존재하지 않는 `instrument_id`를
    대상으로 불림 — 조용히 무시하지 않고 fail-closed로 예외를 던진다."""


class VenueListingOverlapError(Exception):
    """`add_listing()`이 같은 (venue, venue_symbol)에 겹치는 기간을
    주장함 — `venue_listings`의 `EXCLUDE USING gist` 제약(DC-4) 위반."""


def _row_to_instrument(row: asyncpg.Record) -> Instrument:
    return Instrument(
        instrument_id=row["instrument_id"],
        asset_class=AssetClass(row["asset_class"]),
        base=row["base"],
        quote=row["quote"],
        isin=row["isin"],
        figi=row["figi"],
        tick_size=row["tick_size"],
        lot_size=row["lot_size"],
        calendar_id=row["calendar_id"],
        lifecycle_state=InstrumentLifecycle(row["lifecycle_state"]),
        created_at=row["created_at"],
    )


def _row_to_listing(row: asyncpg.Record) -> VenueListing:
    return VenueListing(
        instrument_id=row["instrument_id"],
        venue=Venue(row["venue"]),
        venue_symbol=row["venue_symbol"],
        listed_at=row["listed_at"],
        delisted_at=row["delisted_at"],
        is_primary=row["is_primary"],
    )


class PostgresInstrumentRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def get(self, conn: asyncpg.Connection, instrument_id: str) -> Instrument | None:
        row = await conn.fetchrow(
            "SELECT * FROM instruments WHERE instrument_id = $1", instrument_id
        )
        return None if row is None else _row_to_instrument(row)

    async def create(self, conn: asyncpg.Connection, instrument: Instrument) -> Instrument:
        try:
            row = await conn.fetchrow(
                "INSERT INTO instruments "
                "(instrument_id, asset_class, base, quote, isin, figi, tick_size, "
                " lot_size, calendar_id, lifecycle_state, created_at) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11) RETURNING *",
                instrument.instrument_id,
                instrument.asset_class.value,
                instrument.base,
                instrument.quote,
                instrument.isin,
                instrument.figi,
                instrument.tick_size,
                instrument.lot_size,
                instrument.calendar_id,
                instrument.lifecycle_state.value,
                instrument.created_at,
            )
        except asyncpg.exceptions.UniqueViolationError as exc:
            raise DuplicateInstrumentIdError(
                f"이미 등록된 instrument_id: {instrument.instrument_id}"
            ) from exc
        return _row_to_instrument(row)

    async def update_lifecycle_state(
        self,
        conn: asyncpg.Connection,
        instrument_id: str,
        *,
        expected_state: InstrumentLifecycle,
        state: InstrumentLifecycle,
    ) -> Instrument:
        row = await conn.fetchrow(
            "UPDATE instruments SET lifecycle_state = $1 "
            "WHERE instrument_id = $2 AND lifecycle_state = $3 RETURNING *",
            state.value,
            instrument_id,
            expected_state.value,
        )
        if row is not None:
            return _row_to_instrument(row)
        exists = await conn.fetchval(
            "SELECT 1 FROM instruments WHERE instrument_id = $1", instrument_id
        )
        if not exists:
            raise InstrumentNotFoundError(f"instrument_id 없음: {instrument_id}")
        raise ConcurrencyConflictError(
            f"instruments.instrument_id={instrument_id}: lifecycle_state가 기대값"
            f"({expected_state.value})과 다릅니다(동시 처리 충돌) — 다시 조회 후 시도하세요."
        )

    async def get_listing(
        self, conn: asyncpg.Connection, venue: Venue, venue_symbol: str, at: AwareDatetime
    ) -> VenueListing | None:
        row = await conn.fetchrow(
            "SELECT * FROM venue_listings "
            "WHERE venue = $1 AND venue_symbol = $2 "
            "AND listed_at <= $3 AND (delisted_at IS NULL OR $3 < delisted_at)",
            venue.value,
            venue_symbol,
            at,
        )
        return None if row is None else _row_to_listing(row)

    async def add_listing(self, conn: asyncpg.Connection, listing: VenueListing) -> VenueListing:
        try:
            row = await conn.fetchrow(
                "INSERT INTO venue_listings "
                "(instrument_id, venue, venue_symbol, listed_at, delisted_at, is_primary) "
                "VALUES ($1,$2,$3,$4,$5,$6) RETURNING *",
                listing.instrument_id,
                listing.venue.value,
                listing.venue_symbol,
                listing.listed_at,
                listing.delisted_at,
                listing.is_primary,
            )
        except asyncpg.exceptions.ExclusionViolationError as exc:
            raise VenueListingOverlapError(
                f"겹치는 상장 기간: venue={listing.venue.value} "
                f"venue_symbol={listing.venue_symbol}"
            ) from exc
        return _row_to_listing(row)
