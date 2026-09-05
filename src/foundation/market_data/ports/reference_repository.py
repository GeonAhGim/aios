"""LA-9 — 인스트루먼트·별칭·기업행위 참조데이터 저장소 포트.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.2, §9.2 LA-9.

domain/application은 이 Protocol만 알고, 실제 구현
(adapters/postgres_reference_repository.py, LA-12)은 모른다(71번 §4).
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable
from uuid import UUID

import asyncpg
from pydantic import AwareDatetime, BaseModel

from src.foundation.market_data.contracts.v1 import (
    CorporateAction,
    InstrumentRef,
    RegisterInstrumentCommand,
    SymbolStatus,
    Venue,
)


@runtime_checkable
class ReferenceRepository(Protocol):
    async def get_instrument(
        self, conn: asyncpg.Connection, venue: Venue, canonical: str, at: AwareDatetime
    ) -> InstrumentRef | None:
        """`at` 시점에 유효한(별칭 포함) 인스트루먼트. 없으면 `None`."""
        ...

    async def register(
        self, conn: asyncpg.Connection, cmd: RegisterInstrumentCommand
    ) -> InstrumentRef:
        """신규 등록. 같은 (venue, venue_symbol)이 이미 있으면 어댑터가
        예외를 던진다 — 상태 전이는 별도 리프(생애주기) 소관."""
        ...

    async def add_alias(
        self, conn: asyncpg.Connection, instrument_id: UUID, venue: Venue, venue_symbol: str
    ) -> None:
        """RENAME 등으로 생긴 과거 심볼도 조회 가능하게 남긴다(A3)."""
        ...

    async def list_actions(
        self, conn: asyncpg.Connection, instrument_id: UUID
    ) -> list[CorporateAction]:
        """`ex_date` 오름차순. 없으면 빈 리스트."""
        ...

    async def record_action(
        self, conn: asyncpg.Connection, action: CorporateAction
    ) -> CorporateAction:
        """§5 `(instrument_id, action_type, ex_date)` 멱등 — 이미 있으면
        digest 비교 후 기존 값을 그대로 반환한다(재실행이 새 행을 만들지
        않는다)."""
        ...


class SymbolAliasRef(BaseModel):
    """LA-24 — `md_symbol_alias` 1행의 읽기 표현. `contracts/v1.py`가 이미
    300줄 상한(P6.line_cap)에 닿아 있어 `ports/coverage_repository.CoverageSpan`
    과 같은 방식으로 포트가 읽기 계약을 직접 정의한다."""

    alias_id: UUID
    instrument_id: UUID
    venue: Venue
    alias_symbol: str
    valid_from: AwareDatetime
    valid_to: AwareDatetime | None


@runtime_checkable
class ReferenceReadRepository(Protocol):
    """LA-24 — HTTP 읽기 API 전용 조회 포트. `ReferenceRepository`(위, LA-9)는
    수집·등록 경로의 계약이라 그대로 두고(기존 fake·isinstance 테스트 불변),
    목록·별칭·id 조회만 여기로 분리한다. 구현: adapters/postgres_reference_reader.py."""

    async def get_by_id(
        self, conn: asyncpg.Connection, instrument_id: UUID
    ) -> InstrumentRef | None:
        """`md_instrument.instrument_id`로 직접 조회. 없으면 `None`."""
        ...

    async def list_instruments(
        self,
        conn: asyncpg.Connection,
        *,
        venues: frozenset[Venue],
        status: SymbolStatus | None,
        after: UUID | None,
        limit: int,
    ) -> list[InstrumentRef]:
        """`venues` 안의 인스트루먼트를 (venue, canonical_symbol, instrument_id)
        오름차순 keyset으로 `limit`개까지. `after`는 직전 페이지 마지막
        `instrument_id`(커서). `venues`가 비면 빈 리스트(fail-closed)."""
        ...

    async def list_aliases(
        self, conn: asyncpg.Connection, instrument_id: UUID
    ) -> list[SymbolAliasRef]:
        """`valid_from` 오름차순. 없으면 빈 리스트."""
        ...
