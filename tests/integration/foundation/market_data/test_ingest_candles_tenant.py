"""task-10465 F1(M) — md_candle.tenant_id 전파 (test_ingest_candles.py에서 분할).

Spec: docs/audits/AUDIT_2026-10-01_data_ingest_replay.md §2 F1(M등급).
loc_over_500 래칫(candle_store_support.py 선례, CTO 2026-09-23)과 동일한 이유로
test_ingest_candles.py의 일반 LA-15 테스트와 분리했다. 공용 헬퍼는
ingest_candles_support.py.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from src.foundation.market_data.contracts.v1 import (
    IngestCandlesCommand,
    Timeframe,
    Venue,
    Verdict,
)
from tests.integration.conftest import create_test_tenant
from tests.integration.foundation.market_data.ingest_candles_support import (
    _candle,
    _FakeIngestSource,
    _listed_instrument,
    _run,
    deps,  # noqa: F401 -- pytest fixture, discovered via import
)

__all__ = ["deps"]


async def test_ingest_propagates_tenant_id_to_stored_candles(pool, deps):
    tenant_id = await create_test_tenant(pool, bootstrap_default_hierarchy_rows=False)
    instrument = await _listed_instrument(deps)
    t0 = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    candles = [_candle(t0, "100", "110", "90", "105", "10")]
    cmd = IngestCandlesCommand(
        tenant_id=tenant_id,
        venue=Venue.BITGET,
        canonical_symbol=instrument.canonical_symbol,
        timeframe=Timeframe.M1,
        range_start=t0,
        range_end=t0 + timedelta(minutes=1),
        trace_id=uuid.uuid4(),
    )

    result = await _run(deps, cmd, _FakeIngestSource(candles), clock_at=t0)

    assert result.verdict.verdict == Verdict.ACCEPT
    async with pool.acquire() as conn:
        stored_tenant_id = await conn.fetchval(
            "SELECT tenant_id FROM md_candle WHERE batch_id = $1", result.batch_id
        )
    assert stored_tenant_id == tenant_id


async def test_ingest_leaves_candle_tenant_id_null_for_platform_shared_data(pool, deps):
    """`tenant_id=None`은 "모름"이 아니라 "플랫폼 공유 데이터"라는 합법적인
    값이다(`IngestCandlesCommand.tenant_id` docstring) — F1 보정이 이 의미를
    깨고 NULL을 다른 값으로 지어내 저장하면 안 된다."""
    instrument = await _listed_instrument(deps)
    t0 = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    candles = [_candle(t0, "100", "110", "90", "105", "10")]
    cmd = IngestCandlesCommand(
        tenant_id=None,
        venue=Venue.BITGET,
        canonical_symbol=instrument.canonical_symbol,
        timeframe=Timeframe.M1,
        range_start=t0,
        range_end=t0 + timedelta(minutes=1),
        trace_id=uuid.uuid4(),
    )

    result = await _run(deps, cmd, _FakeIngestSource(candles), clock_at=t0)

    async with pool.acquire() as conn:
        stored_tenant_id = await conn.fetchval(
            "SELECT tenant_id FROM md_candle WHERE batch_id = $1", result.batch_id
        )
    assert stored_tenant_id is None
