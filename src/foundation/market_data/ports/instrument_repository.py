"""DC-5 — 심볼 마스터(`Instrument`/`VenueListing`) 저장 포트.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§2.1 DC-5, §3.2(계약), §4.1·§4.2(불변조건), §9.2 DC-5.

domain/application은 이 Protocol만 알고 실제 구현(adapters/storage/*)은
모른다(71번 §4). `contracts/v2/instruments`(DC-1)를 그대로 쓴다 —
`instrument_id` 불변, `venue_listings` 기간 겹침 금지(§4.1)는 DB의 EXCLUDE
제약(DC-4)이 실제로 강제하고 이 Protocol은 계약 형태만 표현한다.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

import asyncpg
from pydantic import AwareDatetime

from src.foundation.market_data.contracts.v1 import Venue
from src.foundation.market_data.contracts.v2.instruments import (
    Instrument,
    InstrumentLifecycle,
    VenueListing,
)


@runtime_checkable
class InstrumentRepository(Protocol):
    async def get(self, conn: asyncpg.Connection, instrument_id: str) -> Instrument | None:
        """없으면 `None`."""
        ...

    async def create(self, conn: asyncpg.Connection, instrument: Instrument) -> Instrument:
        """신규 발급. 같은 `instrument_id` 재삽입은 어댑터가 예외를 던진다
        (§4.1 `instrument_id` 불변 — 이 메서드에 UPDATE 경로는 없다)."""
        ...

    async def update_lifecycle_state(
        self, conn: asyncpg.Connection, instrument_id: str, state: InstrumentLifecycle
    ) -> Instrument:
        """§4.2 전이표를 이미 통과한 결과만 여기로 온다 — 전이 자체의 검증은
        DC-3(`domain/instruments/lifecycle.py`) 소관, 이 메서드는 저장만."""
        ...

    async def get_listing(
        self, conn: asyncpg.Connection, venue: Venue, venue_symbol: str, at: AwareDatetime
    ) -> VenueListing | None:
        """`at` 시점에 유효한(`listed_at <= at`이고 `delisted_at`이 `NULL`이거나
        `at` 이후인) listing. 없으면 `None`."""
        ...

    async def add_listing(self, conn: asyncpg.Connection, listing: VenueListing) -> VenueListing:
        """심볼 변경은 구 listing에 `delisted_at`을 채운 뒤 새 listing을
        추가하는 방식(§3.2) — 이 메서드는 추가만 하고, 구 listing 종료는
        호출자가 별도로 한다."""
        ...
