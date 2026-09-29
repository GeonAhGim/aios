"""`PostgresBatchRepository` negative/실패주입 보강 — task-7947 DEEPEN(원 리프
task-6704, 고아 산출물 회수 qa-2).

task-8658/8660 선례와 동일한 결함: 이 디렉터리의 `__init__.py`는
negative/실패주입 테스트를 담기에 부적절한 위치다 — pytest 기본
`python_files`(=`test_*.py`)는 `__init__.py`를 test 모듈로 수집하지 않아
(`pytest --collect-only`로 실측 확인) 거기 있던 어떤 테스트도 실행되지
않는다. `test_*.py`로 새로 만들어 실제로 수집·실행되게 한다.

대상: `src/foundation/market_data/adapters/postgres_batch_repository.py`
(LA-13). §4.1 fail-closed 두 축을 검증한다 — (1) `audit_event_id`가 없는
배치는 DB에 보내기 전에 애플리케이션 계층에서 거부되어야 하고, (2) DB가
예상 못한 오류를 던지면(예: 커넥션 장애) `DuplicateBatchError`로 조용히
둔갑시키지 않고 원래 예외 그대로 전파해야 한다(그래야 호출자가 "정말
중복인지" vs "그냥 DB가 죽었는지"를 구분할 수 있다).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import asyncpg
import pytest

from src.foundation.market_data.adapters.postgres_batch_repository import (
    DuplicateBatchError,
    PostgresBatchRepository,
)
from src.foundation.market_data.contracts.v1 import (
    IngestBatchResult,
    QualityVerdict,
    Timeframe,
    Venue,
    Verdict,
)


def _batch(*, audit_event_id: uuid.UUID | None) -> IngestBatchResult:
    now = datetime.now(timezone.utc)
    return IngestBatchResult(
        batch_id=uuid.uuid4(),
        tenant_id=None,
        source=Venue.BITGET.value,
        venue=Venue.BITGET,
        instrument_id=uuid.uuid4(),
        timeframe=Timeframe.M1,
        range_start=now - timedelta(minutes=1),
        range_end=now,
        request_fingerprint=f"fp-{uuid.uuid4().hex}",
        verdict=QualityVerdict(
            verdict=Verdict.ACCEPT, accepted=1, quarantined=0, rejected=0, issues=[]
        ),
        batch_hash="deadbeef",
        audit_event_id=audit_event_id,
        stored_range=None,
    )


# --- negative tests (§4.1 fail-closed: audit_event_id 없는 배치 거부) --------


async def test_create_rejects_batch_missing_audit_event_id(deps):
    """`md_ingest_batch.audit_event_id`는 NOT NULL — 없으면 DB에 보내기 전에
    애플리케이션에서 즉시 거부해야 한다(위반 시 FK 오류로 늦게, 불명확하게
    실패하는 대신)."""
    repo = PostgresBatchRepository(deps.pool)
    batch = _batch(audit_event_id=None)

    async with deps.pool.acquire() as conn:
        with pytest.raises(ValueError, match="audit_event_id"):
            await repo.create(conn, batch)


async def test_create_tick_batch_rejects_missing_audit_event_id(deps):
    """`create_tick_batch`도 같은 NOT NULL 불변식을 공유한다(md_ingest_batch_tick)."""
    from src.foundation.market_data.contracts.v1 import TickIngestBatchResult

    repo = PostgresBatchRepository(deps.pool)
    now = datetime.now(timezone.utc)
    tick_batch = TickIngestBatchResult(
        batch_id=uuid.uuid4(),
        tenant_id=None,
        source=Venue.BITGET.value,
        venue=Venue.BITGET,
        instrument_id=uuid.uuid4(),
        range_start=now - timedelta(minutes=1),
        range_end=now,
        request_fingerprint=f"fp-{uuid.uuid4().hex}",
        verdict=QualityVerdict(
            verdict=Verdict.ACCEPT, accepted=1, quarantined=0, rejected=0, issues=[]
        ),
        batch_hash="deadbeef",
        audit_event_id=None,
    )

    async with deps.pool.acquire() as conn:
        with pytest.raises(ValueError, match="audit_event_id"):
            await repo.create_tick_batch(conn, tick_batch)


async def test_calendar_upsert_days_rejects_venue_mismatch(deps):
    """`PostgresCalendarRepository.upsert_days`(같은 market_data 통합 경계)도
    호출 인자 `venue`와 각 `CalendarDay.venue`가 다르면 DB에 섞어 쓰지 않고
    명시적으로 거부해야 한다."""
    from src.foundation.market_data.contracts.v1 import CalendarDay

    day = CalendarDay(
        venue=Venue.KIS_KRX,
        trade_date=datetime.now(timezone.utc).date(),
        is_trading_day=True,
        open_at=None,
        close_at=None,
        early_close=False,
        source="test",
    )

    async with deps.pool.acquire() as conn:
        with pytest.raises(ValueError, match="venue"):
            await deps.cal.upsert_days(conn, Venue.BITGET, [day])


# --- 실패주입 (예상 못한 DB 오류를 DuplicateBatchError로 둔갑시키지 않음) ----


class _BoomOnExecuteConn:
    """`execute`가 `UniqueViolationError`가 아닌 임의 예외를 던지도록 흉내내는
    가짜 커넥션 — `create()`의 `except asyncpg.exceptions.UniqueViolationError`가
    다른 예외 종류까지 삼켜 `DuplicateBatchError`로 둔갑시키지 않는지 검증한다."""

    async def execute(self, *args: object, **kwargs: object) -> None:
        raise asyncpg.exceptions.ConnectionDoesNotExistError("injected connection loss")


async def test_create_does_not_mask_non_duplicate_db_errors_as_duplicate(deps):
    repo = PostgresBatchRepository(deps.pool)
    batch = _batch(audit_event_id=uuid.uuid4())
    conn = _BoomOnExecuteConn()

    with pytest.raises(asyncpg.exceptions.ConnectionDoesNotExistError):
        await repo.create(conn, batch)  # type: ignore[arg-type]


async def test_create_raises_duplicate_batch_error_on_pk_collision(deps):
    """실제 §4.1 fail-closed 대상 시나리오: 같은 `batch_id`로 두 번째 `create()`를
    부르면(append-only 위반) 의미가 분명한 `DuplicateBatchError`로 거부되어야
    한다 — 이번엔 진짜 `UniqueViolationError` 경로(주입이 아니라 실제 PK 제약)."""
    from src.data.models.base import AssetClass
    from src.foundation.evidence.domain.models import Classification, Outcome
    from src.foundation.market_data.application.register_instrument import (
        register_instrument,
    )
    from src.foundation.market_data.contracts.v1 import RegisterInstrumentCommand

    cmd = RegisterInstrumentCommand(
        venue=Venue.BITGET,
        venue_symbol=f"T{uuid.uuid4().hex[:10].upper()}USDT",
        asset_class=AssetClass.CRYPTO,
        tick_size=Decimal("0.01"),
        lot_size=Decimal("0.0001"),
        listed_at=datetime.now(timezone.utc) - timedelta(days=1),
        actor_subject_id=uuid.uuid4(),
        trace_id=uuid.uuid4(),
    )
    instrument = await register_instrument(deps.pool, cmd, refs=deps.refs, audit=deps.audit)

    audit_event = await deps.audit.append_event(
        tenant_id=None,
        aggregate_type="md_ingest_batch",
        aggregate_id=uuid.uuid4(),
        aggregate_revision=None,
        action="market_data.candles_ingested",
        outcome=Outcome.SUCCESS,
        actor_subject_id=None,
        trace_id=uuid.uuid4(),
        payload_hash="deadbeef",
        payload={},
        classification=Classification.INTERNAL,
    )

    repo = PostgresBatchRepository(deps.pool)
    batch = _batch(audit_event_id=audit_event.id)
    batch = batch.model_copy(update={"instrument_id": instrument.instrument_id})

    async with deps.pool.acquire() as conn:
        await repo.create(conn, batch)

        with pytest.raises(DuplicateBatchError):
            await repo.create(conn, batch)
