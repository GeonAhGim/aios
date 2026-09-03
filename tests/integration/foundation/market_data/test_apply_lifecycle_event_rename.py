"""LA-14 `apply_lifecycle_event` RENAME 경로 통합테스트 — 실 DB(TEST_DATABASE_URL) 대상.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§4.2, §9.2 LA-14.

`test_register_instrument.py`에는 RENAME 이벤트를 실행하는 테스트가 하나도
없었다(커버리지 83% — `apply_lifecycle_event`의 RENAME 분기 전체가 미실행).
그 공백 때문에 실제 결함이 숨어 있었다: RENAME 가드(`get_instrument`로 대상
심볼 사용 중인지 확인)와 신규 별칭 저장(`add_alias`)이 `to_canonical()`로
정규화한 형식을 썼는데, `register()`(LA-12)가 최초 별칭을 심을 때는 venue
원시 심볼(`cmd.venue_symbol`) 그대로를 쓴다 — KRX/US는 canonical==원시라
드러나지 않지만 BASE/QUOTE 슬래시가 붙는 BITGET에서는 두 형식이 달라져
`get_instrument` 조회가 항상 빈 결과를 내고 가드가 무력화됐다(이미 다른
인스트루먼트가 쓰는 원시 심볼로도 RENAME이 그냥 성공). `apply_lifecycle_event`를
`refs.get_instrument`/`refs.add_alias`에 원시 심볼(`cmd.new_venue_symbol`)을
그대로 넘기도록 고쳐 `register()`와 형식을 맞췄다 — `to_canonical` 호출은
형식 검증(`SymbolNormalizationError`) 용도로만 남긴다.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.data.models.base import AssetClass
from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.market_data.adapters.postgres_reference_repository import (
    PostgresReferenceRepository,
)
from src.foundation.market_data.application.register_instrument import (
    RenameSymbolInUseError,
    apply_lifecycle_event,
    register_instrument,
)
from src.foundation.market_data.contracts.v1 import (
    LifecycleEventCommand,
    RegisterInstrumentCommand,
    SymbolStatus,
    Venue,
)


def _bitget_symbol() -> str:
    return f"Z{uuid.uuid4().hex[:8].upper()}USDT"


def _register_cmd(*, venue_symbol: str) -> RegisterInstrumentCommand:
    return RegisterInstrumentCommand(
        venue=Venue.BITGET,
        venue_symbol=venue_symbol,
        asset_class=AssetClass.CRYPTO,
        tick_size=Decimal("0.01"),
        lot_size=Decimal("0.0001"),
        listed_at=datetime.now(timezone.utc) - timedelta(days=1),
        actor_subject_id=uuid.uuid4(),
        trace_id=uuid.uuid4(),
    )


def _lifecycle_cmd(instrument_id, event: str, *, new_venue_symbol: str | None = None):
    return LifecycleEventCommand(
        instrument_id=instrument_id,
        event=event,
        effective_at=datetime.now(timezone.utc),
        new_venue_symbol=new_venue_symbol,
        source_ref=f"test:{event.lower()}",
        actor_subject_id=uuid.uuid4(),
        trace_id=uuid.uuid4(),
    )


@pytest.fixture
def refs(pool):
    return PostgresReferenceRepository(pool)


@pytest.fixture
def audit(pool):
    return PostgresAuditEventRepository(pool)


async def _event_count(pool, aggregate_id) -> int:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT count(*) FROM foundation_audit_event WHERE aggregate_id = $1", aggregate_id
        )


async def _listed(pool, refs, audit, *, venue_symbol: str):
    instrument = await register_instrument(
        pool, _register_cmd(venue_symbol=venue_symbol), refs=refs, audit=audit
    )
    return await apply_lifecycle_event(
        pool, _lifecycle_cmd(instrument.instrument_id, "LIST"),
        current=instrument, refs=refs, audit=audit,
    )


async def test_rename_writes_alias_and_audits_once(pool, refs, audit):
    listed = await _listed(pool, refs, audit, venue_symbol=_bitget_symbol())
    new_symbol = _bitget_symbol()

    renamed = await apply_lifecycle_event(
        pool,
        _lifecycle_cmd(listed.instrument_id, "RENAME", new_venue_symbol=new_symbol),
        current=listed, refs=refs, audit=audit,
    )
    assert renamed.status == SymbolStatus.LISTED
    assert await _event_count(pool, listed.instrument_id) == 3  # registered + listed + renamed

    async with pool.acquire() as conn:
        found = await refs.get_instrument(
            conn, Venue.BITGET, new_symbol, datetime.now(timezone.utc)
        )
    assert found is not None
    assert found.instrument_id == listed.instrument_id


async def test_rename_without_new_venue_symbol_denied_and_audited(pool, refs, audit):
    listed = await _listed(pool, refs, audit, venue_symbol=_bitget_symbol())

    with pytest.raises(RenameSymbolInUseError):
        await apply_lifecycle_event(
            pool,
            _lifecycle_cmd(listed.instrument_id, "RENAME", new_venue_symbol=None),
            current=listed, refs=refs, audit=audit,
        )

    assert await _event_count(pool, listed.instrument_id) == 3  # registered + listed + denied
    async with pool.acquire() as conn:
        last_outcome = await conn.fetchval(
            "SELECT outcome FROM foundation_audit_event WHERE aggregate_id = $1 "
            "ORDER BY sequence_no DESC LIMIT 1",
            listed.instrument_id,
        )
    assert last_outcome == "DENIED"


async def test_rename_rejects_symbol_already_in_use_by_another_instrument(pool, refs, audit):
    """회귀 가드: BITGET처럼 canonical != venue 원시 형식인 venue에서
    RENAME 대상 심볼이 이미 다른 인스트루먼트에 쓰이고 있으면 거부돼야 한다."""
    taken_symbol = _bitget_symbol()
    await _listed(pool, refs, audit, venue_symbol=taken_symbol)
    other = await _listed(pool, refs, audit, venue_symbol=_bitget_symbol())

    with pytest.raises(RenameSymbolInUseError):
        await apply_lifecycle_event(
            pool,
            _lifecycle_cmd(other.instrument_id, "RENAME", new_venue_symbol=taken_symbol),
            current=other, refs=refs, audit=audit,
        )

    assert await _event_count(pool, other.instrument_id) == 3  # registered + listed + denied


async def test_apply_lifecycle_event_instrument_id_mismatch_rejected(pool, refs, audit):
    listed = await _listed(pool, refs, audit, venue_symbol=_bitget_symbol())
    mismatched_cmd = _lifecycle_cmd(uuid.uuid4(), "LIST")

    with pytest.raises(ValueError, match="instrument_id"):
        await apply_lifecycle_event(
            pool, mismatched_cmd, current=listed, refs=refs, audit=audit,
        )
