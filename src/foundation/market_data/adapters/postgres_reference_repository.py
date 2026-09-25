"""LA-12 — asyncpg implementation of `ReferenceRepository` (ports/reference_repository.py).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.2, §5, §9.2 LA-12.

`md_instrument.venue_symbol` is an immutable audit field that preserves the
original symbol at registration time (this leaf has no method to update it —
updating the "current" symbol after RENAME is the LA-14 application layer's
responsibility); all point-in-time (`at`) symbol lookups go through
`md_symbol_alias` (LA-10 `EXCLUDE USING gist` period exclusion).
`register()` also seeds the original symbol as the first alias
(`valid_from=listed_at, valid_to=NULL`) so that `get_instrument` returns
the same result via the alias path from the moment of registration
(design intent of migration 4a1d0c0de007).

`get_instrument` finds only rows in `md_symbol_alias` where `venue`+`alias_symbol`
are valid at the `at` point in time and returns the instrument by that
`instrument_id` — it does not match against `md_instrument.canonical_symbol`
directly (that column is not updated after RENAME; allowing direct matches
would make old symbols appear perpetually valid regardless of period,
breaking the period accuracy of RENAME aliases).
Basis for this design: §9.2 LA-12 DoD — "symbol RENAME aliases are
correctly interpreted via period (valid_from/valid_to)".
"""
from __future__ import annotations

from uuid import UUID

import asyncpg
from pydantic import AwareDatetime

from src.data.models.base import AssetClass
from src.foundation.market_data.contracts.v1 import (
    CorporateAction,
    InstrumentRef,
    RegisterInstrumentCommand,
    SymbolStatus,
    Venue,
)
from src.foundation.market_data.domain.reference.symbol_normalizer import to_canonical

__all__ = [
    "AliasPeriodOverlapError",
    "CorporateActionDigestMismatchError",
    "DuplicateInstrumentError",
    "PostgresReferenceRepository",
]


class DuplicateInstrumentError(Exception):
    """`register()` called a second time for the same (venue, venue_symbol) —
    state transitions (RENAME, etc.) belong to `add_alias`, not re-registration."""


class AliasPeriodOverlapError(Exception):
    """`md_symbol_alias` `EXCLUDE USING gist` violation — different instruments
    claim overlapping validity periods for the same (venue, alias_symbol)."""


class CorporateActionDigestMismatchError(Exception):
    """`(instrument_id, action_type, ex_date)` resent with different ratio/cash_amount/
    source_ref — do not silently overwrite the existing value (fail-closed)."""


def _split_base_quote(venue: Venue, canonical_symbol: str) -> tuple[str | None, str | None]:
    """Decompose only crypto (`BASE/QUOTE`) — KRX/US canonical has no
    base/quote concept and always returns `None`."""
    if venue is Venue.BITGET and "/" in canonical_symbol:
        base, _, quote = canonical_symbol.partition("/")
        return base, quote
    return None, None


def _row_to_instrument(row: asyncpg.Record) -> InstrumentRef:
    return InstrumentRef(
        instrument_id=row["instrument_id"],
        venue=Venue(row["venue"]),
        canonical_symbol=row["canonical_symbol"],
        venue_symbol=row["venue_symbol"],
        asset_class=AssetClass(row["asset_class"]),
        base=row["base"],
        quote=row["quote"],
        tick_size=row["tick_size"],
        lot_size=row["lot_size"],
        status=SymbolStatus(row["status"]),
        listed_at=row["listed_at"],
        delisted_at=row["delisted_at"],
    )


def _row_to_action(row: asyncpg.Record) -> CorporateAction:
    return CorporateAction(
        action_type=row["action_type"],
        instrument_id=row["instrument_id"],
        ex_date=row["ex_date"],
        ratio=row["ratio"],
        cash_amount=row["cash_amount"],
        source_ref=row["source_ref"],
    )


async def _insert_alias(
    conn: asyncpg.Connection,
    *,
    instrument_id: UUID,
    venue: Venue,
    alias_symbol: str,
    valid_from: AwareDatetime,
) -> None:
    try:
        await conn.execute(
            "INSERT INTO md_symbol_alias (instrument_id, venue, alias_symbol, valid_from) "
            "VALUES ($1, $2, $3, $4)",
            instrument_id,
            venue.value,
            alias_symbol,
            valid_from,
        )
    except asyncpg.exceptions.ExclusionViolationError as exc:
        raise AliasPeriodOverlapError(
            f"Alias period overlap: venue={venue.value} alias_symbol={alias_symbol}"
        ) from exc


class PostgresReferenceRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def get_instrument(
        self, conn: asyncpg.Connection, venue: Venue, canonical: str, at: AwareDatetime
    ) -> InstrumentRef | None:
        """`md_instrument.canonical_symbol` is fixed at registration and never
        updated after RENAME (this leaf has no such method) — do not match it
        directly. Instead, `md_symbol_alias` (including the first alias at
        registration, see `register()`) is the real source. This ensures that
        lookups by an old symbol return exactly `None` outside its validity
        period (prevents the bug where an old symbol appears perpetually valid
        after RENAME)."""
        alias_row = await conn.fetchrow(
            "SELECT instrument_id FROM md_symbol_alias WHERE venue = $1 AND alias_symbol = $2 "
            "AND valid_from <= $3 AND (valid_to IS NULL OR $3 < valid_to)",
            venue.value,
            canonical,
            at,
        )
        if alias_row is None:
            return None
        row = await conn.fetchrow(
            "SELECT * FROM md_instrument WHERE instrument_id = $1", alias_row["instrument_id"]
        )
        return None if row is None else _row_to_instrument(row)

    async def register(
        self, conn: asyncpg.Connection, cmd: RegisterInstrumentCommand
    ) -> InstrumentRef:
        canonical_symbol = to_canonical(cmd.venue, cmd.venue_symbol)

        already = await conn.fetchval(
            "SELECT 1 FROM md_instrument WHERE venue = $1 AND venue_symbol = $2",
            cmd.venue.value,
            cmd.venue_symbol,
        )
        if already:
            raise DuplicateInstrumentError(
                f"Already registered: venue={cmd.venue.value} venue_symbol={cmd.venue_symbol}"
            )

        base, quote = _split_base_quote(cmd.venue, canonical_symbol)

        row = await conn.fetchrow(
            "INSERT INTO md_instrument "
            "(venue, canonical_symbol, venue_symbol, asset_class, base, quote, "
            " tick_size, lot_size, status, listed_at) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10) RETURNING *",
            cmd.venue.value,
            canonical_symbol,
            cmd.venue_symbol,
            cmd.asset_class.value,
            base,
            quote,
            cmd.tick_size,
            cmd.lot_size,
            SymbolStatus.PENDING.value,
            cmd.listed_at,
        )

        await _insert_alias(
            conn,
            instrument_id=row["instrument_id"],
            venue=cmd.venue,
            alias_symbol=cmd.venue_symbol,
            valid_from=cmd.listed_at,
        )

        return _row_to_instrument(row)

    async def add_alias(
        self, conn: asyncpg.Connection, instrument_id: UUID, venue: Venue, venue_symbol: str
    ) -> None:
        effective_at = await conn.fetchval("SELECT now()")
        await conn.execute(
            "UPDATE md_symbol_alias SET valid_to = $1 "
            "WHERE instrument_id = $2 AND venue = $3 AND valid_to IS NULL",
            effective_at,
            instrument_id,
            venue.value,
        )
        await _insert_alias(
            conn,
            instrument_id=instrument_id,
            venue=venue,
            alias_symbol=venue_symbol,
            valid_from=effective_at,
        )

    async def list_actions(
        self, conn: asyncpg.Connection, instrument_id: UUID
    ) -> list[CorporateAction]:
        rows = await conn.fetch(
            "SELECT * FROM md_corporate_action WHERE instrument_id = $1 ORDER BY ex_date ASC",
            instrument_id,
        )
        return [_row_to_action(row) for row in rows]

    async def record_action(
        self, conn: asyncpg.Connection, action: CorporateAction
    ) -> CorporateAction:
        existing = await conn.fetchrow(
            "SELECT * FROM md_corporate_action "
            "WHERE instrument_id = $1 AND action_type = $2 AND ex_date = $3",
            action.instrument_id,
            action.action_type,
            action.ex_date,
        )
        if existing is not None:
            existing_action = _row_to_action(existing)
            if (
                existing_action.ratio,
                existing_action.cash_amount,
                existing_action.source_ref,
            ) != (action.ratio, action.cash_amount, action.source_ref):
                raise CorporateActionDigestMismatchError(
                    f"Resent with different content: instrument_id={action.instrument_id} "
                    f"action_type={action.action_type} ex_date={action.ex_date}"
                )
            return existing_action

        row = await conn.fetchrow(
            "INSERT INTO md_corporate_action "
            "(instrument_id, action_type, ex_date, ratio, cash_amount, source_ref) "
            "VALUES ($1,$2,$3,$4,$5,$6) RETURNING *",
            action.instrument_id,
            action.action_type,
            action.ex_date,
            action.ratio,
            action.cash_amount,
            action.source_ref,
        )
        return _row_to_action(row)
