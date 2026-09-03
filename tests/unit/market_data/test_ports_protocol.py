"""LA-9 ports/*.py 구조적 계약 테스트.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.2, §9.2 LA-9.

`@runtime_checkable` Protocol의 `isinstance()`는 메서드 **이름**만 확인한다 —
파라미터·반환 타입은 mypy(정적)가 확인한다
(`tests/foundation/unit/positions/test_ports_protocol.py`와 같은 패턴). 그래서
negative test는 두 종류다: (1) 메서드 하나가 빠진 구현은 isinstance()에서부터
False가 되는 fail-closed 사례, (2) 메서드는 다 갖췄지만 DTO 대신 dict를
돌려주는 구현은 isinstance()를 통과해도 그 결과가 계약 DTO(`IngestBatchResult`)
검증은 통과하지 못한다는 사례.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from src.foundation.market_data.contracts.v1 import IngestBatchResult
from src.foundation.market_data.ports.batch_repository import BatchRepository
from src.foundation.market_data.ports.calendar_repository import CalendarRepository
from src.foundation.market_data.ports.candle_store import CandleStore
from src.foundation.market_data.ports.ingest_source import IngestSource
from src.foundation.market_data.ports.reference_repository import ReferenceRepository


def _now() -> datetime:
    return datetime(2026, 9, 3, 0, 0, tzinfo=timezone.utc)


class _FullCandleStore:
    async def upsert_batch(self, conn, batch_id, candles): ...
    async def quarantine(self, conn, batch_id, candles, issues): ...
    async def query(self, conn, key, start, end, as_of): ...
    async def last_open_time(self, conn, key): ...


class _MissingLastOpenTimeCandleStore:
    """`last_open_time`이 빠진 불완전 구현 — 포트를 만족하지 못해야 한다."""

    async def upsert_batch(self, conn, batch_id, candles): ...
    async def quarantine(self, conn, batch_id, candles, issues): ...
    async def query(self, conn, key, start, end, as_of): ...


class _FullReferenceRepository:
    async def get_instrument(self, conn, venue, canonical, at): ...
    async def register(self, conn, cmd): ...
    async def add_alias(self, conn, instrument_id, venue, venue_symbol): ...
    async def list_actions(self, conn, instrument_id): ...
    async def record_action(self, conn, action): ...


class _FullCalendarRepository:
    async def load(self, conn, venue, year): ...
    async def upsert_days(self, conn, venue, days): ...


class _FullIngestSource:
    async def fetch_candles(self, venue, raw_symbol, tf, start, end): ...


class _FullBatchRepository:
    async def create(self, conn, batch): ...
    async def add_issues(self, conn, batch_id, issues): ...
    async def get(self, conn, batch_id, tenant_id): ...
    async def create_tick_batch(self, conn, batch): ...
    async def get_tick_batch(self, conn, batch_id, tenant_id): ...


class _DictReturningBatchRepository:
    """메서드 이름은 전부 갖췄으니 `isinstance()`는 통과하지만, `get`이
    `IngestBatchResult` 대신 얕은 dict를 돌려준다 — mypy가 없으면 구조 검사만
    으로는 이 차이를 잡지 못한다는 것을 보이는 fixture."""

    async def create(self, conn, batch): ...
    async def add_issues(self, conn, batch_id, issues): ...
    async def create_tick_batch(self, conn, batch): ...
    async def get_tick_batch(self, conn, batch_id, tenant_id): ...

    async def get(self, conn, batch_id, tenant_id):
        return {"batch_id": str(batch_id)}


def test_full_implementations_satisfy_their_ports() -> None:
    assert isinstance(_FullCandleStore(), CandleStore)
    assert isinstance(_FullReferenceRepository(), ReferenceRepository)
    assert isinstance(_FullCalendarRepository(), CalendarRepository)
    assert isinstance(_FullIngestSource(), IngestSource)
    assert isinstance(_FullBatchRepository(), BatchRepository)


def test_incomplete_implementation_fails_port_check() -> None:
    """포트 메서드 하나 누락 → isinstance() False(fail-closed 구조 증명)."""
    assert not isinstance(_MissingLastOpenTimeCandleStore(), CandleStore)


async def test_dict_returning_fake_satisfies_isinstance_but_not_the_dto() -> None:
    """DoD negative test: dict를 돌려주는 가짜 구현은 구조적으로는 포트를
    만족한다고 판정되지만(메서드 이름만 검사하므로), 그 결과값은 계약 DTO
    검증을 통과하지 못한다 — Protocol을 "진짜로" 만족한다고 볼 수 없다."""
    fake = _DictReturningBatchRepository()
    assert isinstance(fake, BatchRepository)

    result = await fake.get(conn=None, batch_id=_now(), tenant_id=None)
    assert isinstance(result, dict)
    with pytest.raises(ValidationError):
        IngestBatchResult.model_validate(result)
