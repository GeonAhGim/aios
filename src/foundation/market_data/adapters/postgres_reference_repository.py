"""LA-12 — `ReferenceRepository`(ports/reference_repository.py)의 asyncpg 구현.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.2, §5, §9.2 LA-12.

`md_instrument.venue_symbol`은 등록 시점의 원 심볼을 그대로 보존하는
불변 감사 필드이고(이 리프에는 그것을 갱신하는 메서드가 없다 — RENAME 후
"현재" 심볼 갱신은 LA-14 애플리케이션 계층 소관), 시점(`at`)별 심볼 해석은
전부 `md_symbol_alias`(LA-10 `EXCLUDE USING gist` 기간 배제)로 한다.
`register()`가 등록 원 심볼을 최초 별칭(`valid_from=listed_at,
valid_to=NULL`)으로도 함께 심어, 등록 직후부터 `get_instrument`가 별칭
경로로도 동일 결과를 내도록 한다(migration 4a1d0c0de007 설계 의도).

`get_instrument`는 `md_symbol_alias`에서 `venue`+`alias_symbol`이 `at`
시점에 유효한 행만 찾아 그 `instrument_id`로 인스트루먼트를 반환한다 —
`md_instrument.canonical_symbol` 직접 매치는 쓰지 않는다(그 컬럼은 RENAME
후에도 갱신되지 않아 직접 매치를 허용하면 옛 심볼이 기간과 무관하게
영원히 유효한 것처럼 조회돼 RENAME 별칭의 기간 정확성이 깨진다).
§9.2 LA-12 DoD: "심볼 RENAME 별칭이 기간(valid_from/valid_to)으로 정확히
해석됨"이 이 설계의 근거다.
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
    """`register()`가 같은 (venue, venue_symbol)에 대해 두 번째로 불림 —
    상태 전이(RENAME 등)는 `add_alias`의 몫이지 재등록이 아니다."""


class AliasPeriodOverlapError(Exception):
    """`md_symbol_alias`의 `EXCLUDE USING gist` 위반 — 같은 (venue,
    alias_symbol)에 겹치는 유효기간을 서로 다른 인스트루먼트가 주장함."""


class CorporateActionDigestMismatchError(Exception):
    """`(instrument_id, action_type, ex_date)`가 같은데 ratio/cash_amount/
    source_ref가 다르게 재전송됨 — 조용히 기존 값을 덮지 않는다(fail-closed)."""


def _split_base_quote(venue: Venue, canonical_symbol: str) -> tuple[str | None, str | None]:
    """크립토(`BASE/QUOTE`)만 분해한다 — KRX/US canonical은 base/quote 개념이
    없어 항상 `None`."""
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
            f"별칭 기간 중복: venue={venue.value} alias_symbol={alias_symbol}"
        ) from exc


class PostgresReferenceRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def get_instrument(
        self, conn: asyncpg.Connection, venue: Venue, canonical: str, at: AwareDatetime
    ) -> InstrumentRef | None:
        """`md_instrument.canonical_symbol`은 등록 시점에 고정되고 RENAME 후에도
        갱신되지 않으므로(이 리프에 그런 메서드가 없다) 직접 매치는 쓰지
        않는다 — 대신 `md_symbol_alias`(등록 시 최초 별칭 포함, `register()`
        참고)만이 진짜 소스다. 그래야 과거 심볼로 조회할 때 그 유효기간
        밖에서는 정확히 `None`이 나온다(RENAME 후 옛 심볼이 영원히 유효한
        것처럼 보이는 버그를 막는다)."""
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
                f"이미 등록됨: venue={cmd.venue.value} venue_symbol={cmd.venue_symbol}"
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
                    f"다른 내용으로 재전송됨: instrument_id={action.instrument_id} "
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
