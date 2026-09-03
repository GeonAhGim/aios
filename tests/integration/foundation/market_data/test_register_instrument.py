"""LA-14 register_instrument/apply_lifecycle_event/record_corporate_action/
sync_calendar 통합테스트 — 실 DB(TEST_DATABASE_URL) 대상.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§4.2, §9.2 LA-14.
DoD(task-616): 세 유스케이스 각각 감사 이벤트 1:1 + DELIST된 심볼 재등록/
주문가능 전이 거부, negative: 정규화 불가한 venue_symbol·중복 (venue,
canonical_symbol, listed_at) → 거부.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.data.models.base import AssetClass
from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.market_data.adapters.postgres_calendar_repository import (
    PostgresCalendarRepository,
)
from src.foundation.market_data.adapters.postgres_reference_repository import (
    DuplicateInstrumentError,
    PostgresReferenceRepository,
)
from src.foundation.market_data.application.record_corporate_action import (
    CorporateActionConflictError,
    record_corporate_action,
)
from src.foundation.market_data.application.register_instrument import (
    apply_lifecycle_event,
    register_instrument,
)
from src.foundation.market_data.application.sync_calendar import (
    CalendarVenueMismatchError,
    calendar_aggregate_id,
    sync_calendar,
)
from src.foundation.market_data.contracts.v1 import (
    CalendarDay,
    CorporateAction,
    LifecycleEventCommand,
    RegisterInstrumentCommand,
    SymbolStatus,
    Venue,
)
from src.foundation.market_data.domain.reference.lifecycle import LifecycleTransitionError
from src.foundation.market_data.domain.reference.symbol_normalizer import SymbolNormalizationError


def _krx_symbol() -> str:
    return f"{uuid.uuid4().int % 900000 + 100000:06d}"


def _register_cmd(*, venue_symbol: str, listed_at: datetime) -> RegisterInstrumentCommand:
    return RegisterInstrumentCommand(
        venue=Venue.KIS_KRX,
        venue_symbol=venue_symbol,
        asset_class=AssetClass.KR_EQUITY,
        tick_size=Decimal("1"),
        lot_size=Decimal("1"),
        listed_at=listed_at,
        actor_subject_id=uuid.uuid4(),
        trace_id=uuid.uuid4(),
    )


def _lifecycle_cmd(instrument_id, event: str, *, source_ref: str) -> LifecycleEventCommand:
    return LifecycleEventCommand(
        instrument_id=instrument_id,
        event=event,
        effective_at=datetime.now(timezone.utc),
        source_ref=source_ref,
        actor_subject_id=uuid.uuid4(),
        trace_id=uuid.uuid4(),
    )


class _BoomAuditAppender:
    async def append_event_in(self, conn, **kwargs):
        raise RuntimeError("injected audit failure")


@pytest.fixture
def refs(pool):
    return PostgresReferenceRepository(pool)


@pytest.fixture
def cal(pool):
    return PostgresCalendarRepository(pool)


@pytest.fixture
def audit(pool):
    return PostgresAuditEventRepository(pool)


async def _event_count(pool, aggregate_id) -> int:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT count(*) FROM foundation_audit_event WHERE aggregate_id = $1", aggregate_id
        )


async def _registered(pool, refs, audit, *, days_ago: int = 1):
    listed_at = datetime.now(timezone.utc) - timedelta(days=days_ago)
    cmd = _register_cmd(venue_symbol=_krx_symbol(), listed_at=listed_at)
    return await register_instrument(pool, cmd, refs=refs, audit=audit)


async def _listed_instrument(pool, refs, audit):
    instrument = await _registered(pool, refs, audit)
    return await apply_lifecycle_event(
        pool,
        _lifecycle_cmd(instrument.instrument_id, "LIST", source_ref="test:list"),
        current=instrument,
        refs=refs,
        audit=audit,
    )


async def test_register_instrument_writes_exactly_one_audit_event(pool, refs, audit):
    instrument = await _registered(pool, refs, audit)
    assert instrument.status == SymbolStatus.PENDING
    assert await _event_count(pool, instrument.instrument_id) == 1


async def test_register_instrument_duplicate_venue_symbol_rejected(pool, refs, audit):
    cmd = _register_cmd(venue_symbol=_krx_symbol(), listed_at=datetime.now(timezone.utc))
    instrument = await register_instrument(pool, cmd, refs=refs, audit=audit)
    with pytest.raises(DuplicateInstrumentError):
        await register_instrument(pool, cmd, refs=refs, audit=audit)

    assert await _event_count(pool, instrument.instrument_id) == 1


async def test_register_instrument_invalid_symbol_format_rejected(pool, refs, audit):
    cmd = _register_cmd(venue_symbol="NOTASIXDIGIT", listed_at=datetime.now(timezone.utc))
    with pytest.raises(SymbolNormalizationError):
        await register_instrument(pool, cmd, refs=refs, audit=audit)


async def test_register_instrument_rolls_back_with_audit_failure(pool, refs):
    """append_event_in 실패 시 같은 트랜잭션의 md_instrument 행도 함께 사라져야 한다."""
    symbol = _krx_symbol()
    cmd = _register_cmd(venue_symbol=symbol, listed_at=datetime.now(timezone.utc))
    with pytest.raises(RuntimeError):
        await register_instrument(pool, cmd, refs=refs, audit=_BoomAuditAppender())

    async with pool.acquire() as conn:
        row = await conn.fetchval(
            "SELECT 1 FROM md_instrument WHERE venue = $1 AND venue_symbol = $2",
            Venue.KIS_KRX.value, symbol,
        )
    assert row is None


async def test_apply_lifecycle_event_list_transitions_and_audits_once(pool, refs, audit):
    listed = await _listed_instrument(pool, refs, audit)
    assert listed.status == SymbolStatus.LISTED
    assert await _event_count(pool, listed.instrument_id) == 2  # registered + listed


async def test_delisted_instrument_rejects_relisting_with_denied_audit(pool, refs, audit):
    """DELIST된 심볼로의 "주문가능" 전이(LIST)는 거부되고, 거부도 감사(DENIED)로 1건 남는다."""
    listed = await _listed_instrument(pool, refs, audit)
    delisted = await apply_lifecycle_event(
        pool,
        _lifecycle_cmd(listed.instrument_id, "DELIST", source_ref="test:delist"),
        current=listed,
        refs=refs,
        audit=audit,
    )
    assert delisted.status == SymbolStatus.DELISTED

    with pytest.raises(LifecycleTransitionError):
        await apply_lifecycle_event(
            pool,
            _lifecycle_cmd(delisted.instrument_id, "LIST", source_ref="test:relist"),
            current=delisted,
            refs=refs,
            audit=audit,
        )

    # registered + listed + delisted + denied(list 재시도) = 4
    assert await _event_count(pool, delisted.instrument_id) == 4
    async with pool.acquire() as conn:
        last_outcome = await conn.fetchval(
            "SELECT outcome FROM foundation_audit_event WHERE aggregate_id = $1 "
            "ORDER BY sequence_no DESC LIMIT 1",
            delisted.instrument_id,
        )
    assert last_outcome == "DENIED"


async def test_delisted_symbol_reregistration_rejected(pool, refs, audit):
    """DoD 핵심: DELIST된 심볼의 venue_symbol 재등록도 거부된다."""
    listed = await _listed_instrument(pool, refs, audit)
    await apply_lifecycle_event(
        pool,
        _lifecycle_cmd(listed.instrument_id, "DELIST", source_ref="test:delist"),
        current=listed,
        refs=refs,
        audit=audit,
    )

    with pytest.raises(DuplicateInstrumentError):
        await register_instrument(
            pool,
            _register_cmd(venue_symbol=listed.venue_symbol, listed_at=datetime.now(timezone.utc)),
            refs=refs,
            audit=audit,
        )


async def test_record_corporate_action_replay_writes_no_extra_audit(pool, refs, audit):
    instrument = await _registered(pool, refs, audit, days_ago=30)
    action = CorporateAction(
        action_type="SPLIT",
        instrument_id=instrument.instrument_id,
        ex_date=date.today(),
        ratio=Decimal("2"),
        source_ref=f"test:{uuid.uuid4().hex}",
    )

    first = await record_corporate_action(
        pool, action, actor_subject_id=uuid.uuid4(), trace_id=uuid.uuid4(), refs=refs, audit=audit
    )
    assert first.ratio == Decimal("2")
    assert await _event_count(pool, instrument.instrument_id) == 2  # registered + action

    second = await record_corporate_action(
        pool, action, actor_subject_id=uuid.uuid4(), trace_id=uuid.uuid4(), refs=refs, audit=audit
    )
    assert second == first
    assert await _event_count(pool, instrument.instrument_id) == 2  # 재전송(REPLAY)은 감사 없음


async def test_record_corporate_action_conflict_denied_and_audited(pool, refs, audit):
    instrument = await _registered(pool, refs, audit, days_ago=30)
    action = CorporateAction(
        action_type="SPLIT",
        instrument_id=instrument.instrument_id,
        ex_date=date.today(),
        ratio=Decimal("2"),
        source_ref=f"test:{uuid.uuid4().hex}",
    )
    await record_corporate_action(
        pool, action, actor_subject_id=uuid.uuid4(), trace_id=uuid.uuid4(), refs=refs, audit=audit
    )

    conflicting = action.model_copy(update={"ratio": Decimal("3")})
    with pytest.raises(CorporateActionConflictError):
        await record_corporate_action(
            pool, conflicting, actor_subject_id=uuid.uuid4(), trace_id=uuid.uuid4(),
            refs=refs, audit=audit,
        )

    assert await _event_count(pool, instrument.instrument_id) == 3  # registered + action + denied


def _far_future_year() -> int:
    """고정 연도는 결정론적 calendar_aggregate_id에 재실행마다 이벤트가 쌓이므로 피한다."""
    return 2200 + uuid.uuid4().int % 700


async def test_sync_calendar_writes_one_audit_event_per_call(pool, cal, audit):
    year = _far_future_year()
    days = [
        CalendarDay(
            venue=Venue.KIS_US, trade_date=date(year, 1, 1), is_trading_day=False,
            open_at=None, close_at=None, early_close=False, source="TEST",
        )
    ]

    count = await sync_calendar(
        pool, Venue.KIS_US, year, days,
        actor_subject_id=uuid.uuid4(), trace_id=uuid.uuid4(), cal=cal, audit=audit,
    )
    assert count == 1
    assert await _event_count(pool, calendar_aggregate_id(Venue.KIS_US, year)) == 1


async def test_sync_calendar_rejects_venue_mismatch_without_writing(pool, cal, audit):
    year = _far_future_year()
    mismatched = [
        CalendarDay(
            venue=Venue.KIS_US, trade_date=date(year, 1, 1), is_trading_day=False,
            open_at=None, close_at=None, early_close=False, source="TEST",
        )
    ]

    with pytest.raises(CalendarVenueMismatchError):
        await sync_calendar(
            pool, Venue.KIS_KRX, year, mismatched,
            actor_subject_id=uuid.uuid4(), trace_id=uuid.uuid4(), cal=cal, audit=audit,
        )

    assert await _event_count(pool, calendar_aggregate_id(Venue.KIS_KRX, year)) == 0
