"""LA-14 register_instrument/apply_lifecycle_event/record_corporate_action/
sync_calendar 통합테스트 — 실 DB(TEST_DATABASE_URL) 대상.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§4.2, §9.2 LA-14.
DoD(task-616): 세 유스케이스 각각 감사 이벤트 1:1 + DELIST된 심볼 재등록/
주문가능 전이 거부, negative: 정규화 불가한 venue_symbol·중복 (venue,
canonical_symbol, listed_at) → 거부.

DEPTH 감사(task-2723, docs/audit/DEPTH_LA_LB_LC.md#616) D1 판정 보강(task-2971):
"수치 성능 단언 없음; 게이트 적색 재현 없음". `record_fill`/`replay` 계열
선례(task-822/task-1038/task-1405)가 절대 지연 단언은 공유 CI의 네트워크/
디스크 편차로 상시 적색을 만든다는 것을 이미 증명했으므로, 여기서도 절대
시간을 차단 게이트로 쓰지 않는다 — `register_instrument()` 1회가 소비하는
순차 DB 왕복 수(`add_query_logger` 계수, LA-23 `count_replay_round_trips`와
동일 기법)를 구조 회귀 가드로 쓴다(수치 성능 단언). 그 가드가 실제로
작동함은 왕복을 하나 더 내는 `ReferenceRepository`를 끼워 계수가 상한을
넘기는 것으로 증명한다(게이트 적색 재현, I-10 — "있다"가 아니라 "작동함이
증명됨").
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import asyncpg
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
            Venue.KIS_KRX.value,
            symbol,
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
            pool,
            conflicting,
            actor_subject_id=uuid.uuid4(),
            trace_id=uuid.uuid4(),
            refs=refs,
            audit=audit,
        )

    assert await _event_count(pool, instrument.instrument_id) == 3  # registered + action + denied


def _far_future_year() -> int:
    """고정 연도는 결정론적 calendar_aggregate_id에 재실행마다 이벤트가 쌓이므로 피한다."""
    return 2200 + uuid.uuid4().int % 700


async def test_sync_calendar_writes_one_audit_event_per_call(pool, cal, audit):
    year = _far_future_year()
    days = [
        CalendarDay(
            venue=Venue.KIS_US,
            trade_date=date(year, 1, 1),
            is_trading_day=False,
            open_at=None,
            close_at=None,
            early_close=False,
            source="TEST",
        )
    ]

    count = await sync_calendar(
        pool,
        Venue.KIS_US,
        year,
        days,
        actor_subject_id=uuid.uuid4(),
        trace_id=uuid.uuid4(),
        cal=cal,
        audit=audit,
    )
    assert count == 1
    assert await _event_count(pool, calendar_aggregate_id(Venue.KIS_US, year)) == 1


async def test_sync_calendar_rejects_venue_mismatch_without_writing(pool, cal, audit):
    year = _far_future_year()
    mismatched = [
        CalendarDay(
            venue=Venue.KIS_US,
            trade_date=date(year, 1, 1),
            is_trading_day=False,
            open_at=None,
            close_at=None,
            early_close=False,
            source="TEST",
        )
    ]

    with pytest.raises(CalendarVenueMismatchError):
        await sync_calendar(
            pool,
            Venue.KIS_KRX,
            year,
            mismatched,
            actor_subject_id=uuid.uuid4(),
            trace_id=uuid.uuid4(),
            cal=cal,
            audit=audit,
        )

    assert await _event_count(pool, calendar_aggregate_id(Venue.KIS_KRX, year)) == 0


class _PinnedConnectionPool:
    """`register_instrument`는 자체 `pool.acquire()`를 여는 시그니처라(모듈
    docstring 5-9행), 왕복 수를 세려면 미리 얻어 둔 커넥션 하나만 돌려주는
    풀 대역이 필요하다(LA-23 `count_replay_round_trips`와 동일 기법,
    `tests/integration/foundation/market_data/perf_replay_support.py`)."""

    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[asyncpg.Connection]:
        yield self._conn


# 실측 왕복 수를 그대로 상한으로 못박는다(LA-23 `_MAX_REPLAY_ROUND_TRIPS`와
# 동일 관례) — register()의 중복확인 SELECT/INSERT md_instrument/INSERT
# md_symbol_alias, 트랜잭션 BEGIN/COMMIT, append_event_in()의 advisory
# lock/prev_row SELECT/INSERT로 구성된다. 늘리는 방향의 수정 금지 — 늘리면
# 왕복 수 회귀를 이 게이트가 못 잡는다.
_MAX_REGISTER_INSTRUMENT_ROUND_TRIPS = 8


async def _count_register_instrument_round_trips(pool, *, refs, audit) -> int:
    """`register_instrument()` 1회가 소비하는 순차 DB 왕복 수(구조 회귀
    가드). 같은 커넥션에서 워밍업 호출 1회(다른 심볼)를 먼저 흘려 asyncpg
    코덱 조회를 흡수시킨 뒤, 두 번째 호출만 쿼리 로거로 센다(LA-23 선례와
    동일 이유 — perf_replay_support.py 모듈 docstring)."""
    async with pool.acquire() as conn:
        pinned = _PinnedConnectionPool(conn)
        warmup_cmd = _register_cmd(venue_symbol=_krx_symbol(), listed_at=datetime.now(timezone.utc))
        await register_instrument(pinned, warmup_cmd, refs=refs, audit=audit)

        queries: list[str] = []

        def _log(record: object) -> None:
            queries.append(getattr(record, "query", ""))

        conn.add_query_logger(_log)
        try:
            cmd = _register_cmd(venue_symbol=_krx_symbol(), listed_at=datetime.now(timezone.utc))
            await register_instrument(pinned, cmd, refs=refs, audit=audit)
        finally:
            conn.remove_query_logger(_log)

    return len(queries)


async def test_register_instrument_round_trip_count_within_structural_ceiling(pool, refs, audit):
    """수치 성능 단언(DEEPEN 616): 절대 지연 대신 순차 DB 왕복 수 상한을
    쓴다 — record_fill/replay 선례(task-822/task-1038/task-1405)가 절대 ms
    단언은 공유 CI의 네트워크/디스크 편차로 상시 적색을 만든다는 것을 이미
    증명했고, §9.2 LA-14는 이 유스케이스에 절대 지연 목표를 못박아 두지도
    않는다."""
    round_trip_count = await _count_register_instrument_round_trips(pool, refs=refs, audit=audit)
    print(f"register_instrument round trips: {round_trip_count}")
    assert round_trip_count <= _MAX_REGISTER_INSTRUMENT_ROUND_TRIPS


class _ChattyReferenceRepository(PostgresReferenceRepository):
    """negative test 전용 — `register()`가 실제 등록 전에 왕복을 하나 더
    낸다(구조 회귀의 최소 재현)."""

    async def register(self, conn: asyncpg.Connection, cmd: RegisterInstrumentCommand):
        await conn.fetchval("SELECT 1")
        return await super().register(conn, cmd)


async def test_round_trip_gate_detects_extra_query_in_register_instrument(pool, audit):
    """게이트 적색 재현(DEEPEN 616): 왕복을 하나 더 내는 `ReferenceRepository`
    를 끼우면 위 구조 회귀 가드가 상한을 넘겨 실제로 적색이 된다는 증명
    (I-10: 게이트는 "있다"가 아니라 "작동함이 증명됨")."""
    chatty = _ChattyReferenceRepository(pool)
    round_trip_count = await _count_register_instrument_round_trips(pool, refs=chatty, audit=audit)
    assert round_trip_count == _MAX_REGISTER_INSTRUMENT_ROUND_TRIPS + 1
    assert round_trip_count > _MAX_REGISTER_INSTRUMENT_ROUND_TRIPS
