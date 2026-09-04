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
"""
from __future__ import annotations

import asyncpg
from pydantic import AwareDatetime

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
        self, conn: asyncpg.Connection, instrument_id: str, state: InstrumentLifecycle
    ) -> Instrument:
        row = await conn.fetchrow(
            "UPDATE instruments SET lifecycle_state = $1 WHERE instrument_id = $2 RETURNING *",
            state.value,
            instrument_id,
        )
        if row is None:
            raise InstrumentNotFoundError(f"instrument_id 없음: {instrument_id}")
        return _row_to_instrument(row)

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
