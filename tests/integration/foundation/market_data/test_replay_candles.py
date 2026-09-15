"""LA-17 `application/replay_candles.replay` 통합테스트 — 실 DB(TEST_DATABASE_URL).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9.2 LA-17, A5.
DoD(task-624): 해시 결정론(같은 입력 → 같은 바이트), strict 모드에서 갭이
있으면 예외(negative), 미등록 instrument → 명시적 에러(negative).

BITGET(연속 세션)만 쓴다 — `get_candles.py`와 동일 이유로 캘린더 시드가
필요 없다.
"""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import asyncpg
import pytest

from src.foundation.market_data.adapters.postgres_batch_repository import PostgresBatchRepository
from src.foundation.market_data.adapters.postgres_calendar_repository import (
    PostgresCalendarRepository,
)
from src.foundation.market_data.adapters.postgres_candle_store import PostgresCandleStore
from src.foundation.market_data.adapters.postgres_reference_repository import (
    PostgresReferenceRepository,
)
from src.foundation.market_data.application.get_candles import (
    AsOfInFutureError,
    UnknownSeriesError,
)
from src.foundation.market_data.application.replay_candles import ReplayIncompleteError, replay
from src.foundation.market_data.contracts.v1 import (
    CandleRecord,
    IngestBatchResult,
    QualityVerdict,
    ReplayRequest,
    SeriesKey,
    Timeframe,
    Venue,
    Verdict,
)
from src.foundation.market_data.domain.candle_columns import (
    CandleColumns,
    MismatchedColumnLengthError,
)


@pytest.fixture
def candle_store(pool):
    return PostgresCandleStore(pool)


@pytest.fixture
def batch_repo(pool):
    return PostgresBatchRepository(pool)


@pytest.fixture
def reference_repo(pool):
    return PostgresReferenceRepository(pool)


@pytest.fixture
def calendar_repo(pool):
    return PostgresCalendarRepository(pool)


async def _audit_event_id(conn: asyncpg.Connection) -> uuid.UUID:
    return await conn.fetchval(
        "INSERT INTO foundation_audit_event "
        "(sequence_no, aggregate_type, aggregate_id, action, outcome, trace_id, "
        " payload_hash, payload, event_hash) "
        "VALUES ($1, 'test.market_data', gen_random_uuid(), 'test.md.ingest', 'SUCCESS', "
        " gen_random_uuid(), 'deadbeef', '{}'::jsonb, 'deadbeef') RETURNING id",
        uuid.uuid4().int % (2**62),
    )


async def _instrument_id(conn: asyncpg.Connection) -> uuid.UUID:
    symbol = f"TEST-{uuid.uuid4().hex}"
    return await conn.fetchval(
        "INSERT INTO md_instrument "
        "(venue, canonical_symbol, venue_symbol, asset_class, tick_size, lot_size, "
        " status, listed_at) "
        "VALUES ('BITGET', $1, $1, 'CRYPTO', 0.01, 0.0001, 'LISTED', now()) "
        "RETURNING instrument_id",
        symbol,
    )


def _candle(
    key: SeriesKey, open_time: datetime, o: float, h: float, low: float, c: float, v: float
) -> CandleRecord:
    return CandleRecord(
        key=key,
        open_time=open_time,
        close_time=open_time + timedelta(minutes=1),
        open=Decimal(str(o)),
        high=Decimal(str(h)),
        low=Decimal(str(low)),
        close=Decimal(str(c)),
        volume=Decimal(str(v)),
    )


async def _seed_candles(
    conn: asyncpg.Connection, batch_repo, candle_store, *, instrument_id, key, opens
) -> None:
    audit_event_id = await _audit_event_id(conn)
    batch = IngestBatchResult(
        batch_id=uuid.uuid4(),
        source="test",
        venue=Venue.BITGET,
        instrument_id=instrument_id,
        timeframe=Timeframe.M1,
        range_start=opens[0],
        range_end=opens[-1] + timedelta(minutes=1),
        request_fingerprint=f"fp-{uuid.uuid4().hex}",
        verdict=QualityVerdict(
            verdict=Verdict.ACCEPT, accepted=len(opens), quarantined=0, rejected=0, issues=[]
        ),
        batch_hash=f"hash-{uuid.uuid4().hex}",
        audit_event_id=audit_event_id,
        stored_range=None,
    )
    await batch_repo.create(conn, batch)
    candles = [_candle(key, ot, 100, 110, 90, 105, 10) for ot in opens]
    await candle_store.upsert_batch(conn, batch.batch_id, candles)


async def test_replay_series_hash_is_deterministic_across_calls(
    pool, candle_store, batch_repo, reference_repo, calendar_repo
):
    async with pool.acquire() as conn, conn.transaction():
        instrument_id = await _instrument_id(conn)
        key = SeriesKey(venue=Venue.BITGET, instrument_id=instrument_id, timeframe=Timeframe.M1)
        t0 = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        opens = [t0, t0 + timedelta(minutes=1), t0 + timedelta(minutes=2)]
        await _seed_candles(
            conn, batch_repo, candle_store, instrument_id=instrument_id, key=key, opens=opens
        )
        as_of = await conn.fetchval("SELECT now()")

    request = ReplayRequest(key=key, start=t0, end=t0 + timedelta(minutes=3), as_of=as_of)
    first = await replay(
        request, store=candle_store, refs=reference_repo, cal=calendar_repo, pool=pool
    )
    second = await replay(
        request, store=candle_store, refs=reference_repo, cal=calendar_repo, pool=pool
    )

    assert first.series_hash == second.series_hash
    assert first.expected_count == 3
    assert first.missing_count == 0
    assert first.gaps == []


async def test_replay_strict_gap_raises_incomplete(
    pool, candle_store, batch_repo, reference_repo, calendar_repo
):
    """negative: strict 모드는 갭이 있으면 결과를 반환하지 않고 예외를 낸다."""
    async with pool.acquire() as conn, conn.transaction():
        instrument_id = await _instrument_id(conn)
        key = SeriesKey(venue=Venue.BITGET, instrument_id=instrument_id, timeframe=Timeframe.M1)
        t0 = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        # t0+1분을 건너뛴다 — 3분짜리 창에 2개만 저장.
        opens = [t0, t0 + timedelta(minutes=2)]
        await _seed_candles(
            conn, batch_repo, candle_store, instrument_id=instrument_id, key=key, opens=opens
        )
        as_of = await conn.fetchval("SELECT now()")

    request = ReplayRequest(key=key, start=t0, end=t0 + timedelta(minutes=3), as_of=as_of)
    with pytest.raises(ReplayIncompleteError) as exc_info:
        await replay(request, store=candle_store, refs=reference_repo, cal=calendar_repo, pool=pool)
    assert exc_info.value.expected_count == 3
    assert exc_info.value.missing_count == 1


async def test_replay_unknown_series_raises(pool, candle_store, reference_repo, calendar_repo):
    """negative: 한 번도 수집된 적 없는 시계열 → 명시적 에러(리플레이도 동일)."""
    async with pool.acquire() as conn, conn.transaction():
        instrument_id = await _instrument_id(conn)
    key = SeriesKey(venue=Venue.BITGET, instrument_id=instrument_id, timeframe=Timeframe.M1)
    t0 = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    request = ReplayRequest(key=key, start=t0, end=t0 + timedelta(minutes=1), as_of=t0)
    with pytest.raises(UnknownSeriesError):
        await replay(request, store=candle_store, refs=reference_repo, cal=calendar_repo, pool=pool)


async def test_replay_as_of_in_future_raises(pool, candle_store, reference_repo, calendar_repo):
    """negative: `as_of`가 현재보다 미래면 조회 전에 즉시 거부한다(스토어를
    건드리지 않으므로 미등록 instrument여도 무방)."""
    key = SeriesKey(venue=Venue.BITGET, instrument_id=uuid.uuid4(), timeframe=Timeframe.M1)
    t0 = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    future_as_of = t0 + timedelta(days=1)
    request = ReplayRequest(key=key, start=t0, end=t0 + timedelta(minutes=1), as_of=future_as_of)
    with pytest.raises(AsOfInFutureError):
        await replay(request, store=candle_store, refs=reference_repo, cal=calendar_repo, pool=pool)


# ---------- 실패주입(D3) — `read_candles_columnar`가 손상된 데이터를 돌려주는 경우 ----------


class _CorruptedColumnsCandleStore(PostgresCandleStore):
    """`test_get_candles.py`의 동명 클래스와 동일 기법 — `read_candles_columnar`
    (LA-23b)가 배열 길이가 서로 다른 `CandleColumns`를 돌려주는 실제 어댑터
    결함을 시뮬레이션한다. `replay()`도 `load_series`(get_candles.py, 같은
    리프)를 그대로 재사용하므로 이 결함을 삼키지 않고 전파해야 한다."""

    async def read_candles_columnar(
        self,
        conn: asyncpg.Connection,
        key: SeriesKey,
        start: datetime,
        end: datetime,
        as_of: datetime | None,
    ) -> CandleColumns:
        columns = await super().read_candles_columnar(conn, key, start, end, as_of)
        return CandleColumns(
            ts=columns.ts,
            open=columns.open[:-1],
            high=columns.high,
            low=columns.low,
            close=columns.close,
            volume=columns.volume,
            quote_volume=columns.quote_volume,
        )


async def test_replay_raises_when_store_returns_mismatched_columns(
    pool, batch_repo, reference_repo, calendar_repo
):
    """실패주입: 저장소가 배열 길이가 어긋난 컬럼을 돌려주면 `replay`는
    잘못 정렬된 캔들로 `series_hash`를 계산하지 않고
    `MismatchedColumnLengthError`를 그대로 전파해야 한다(fail-closed) —
    A5 "같은 as_of+같은 범위 → 같은 바이트"는 입력 자체가 손상된 경우까지
    보장하지 않는다."""
    corrupted_store = _CorruptedColumnsCandleStore(pool)
    async with pool.acquire() as conn, conn.transaction():
        instrument_id = await _instrument_id(conn)
        key = SeriesKey(venue=Venue.BITGET, instrument_id=instrument_id, timeframe=Timeframe.M1)
        t0 = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        await _seed_candles(
            conn, batch_repo, corrupted_store, instrument_id=instrument_id, key=key, opens=[t0]
        )
        as_of = await conn.fetchval("SELECT now()")

    request = ReplayRequest(key=key, start=t0, end=t0 + timedelta(minutes=1), as_of=as_of)
    with pytest.raises(MismatchedColumnLengthError):
        await replay(
            request, store=corrupted_store, refs=reference_repo, cal=calendar_repo, pool=pool
        )


# ---------- 게이트 적색 재현(D3) — strict 갭 판정을 지우면 negative test가 뒤집히는가 ----------
#
# `tests/unit/exchanges/test_symbol_canonicalization.py`(task-2963/LA-19
# DEEPEN) 선례와 동일 기법이다: 자식 pytest 프로세스 안에서만 소스 문자열
# 치환으로 `replay()`의 strict 갭 검사(§9.2 LA-17, A5)를 지워, 기존
# `test_replay_strict_gap_raises_incomplete`가 green(1 passed)에서
# red(1 failed)로 뒤집히는지 실측한다(프로덕션 소스는 그대로 — 같은
# 인터프리터 프로세스에는 이 변형이 전혀 보이지 않는다).

_THIS_TESTFILE = "tests/integration/foundation/market_data/test_replay_candles.py"

_STRICT_GAP_GUARD = (
    "    missing_count = len(issues)\n"
    "    if missing_count:\n"
    "        raise ReplayIncompleteError(expected_count=expected_total, "
    "missing_count=missing_count)\n"
)
_STRICT_GAP_MUTATED = "    missing_count = len(issues)\n"


def _source_mutation_plugin_source(module_name: str, guard: str, mutated: str) -> str:
    return f"""\
import importlib
from pathlib import Path


def pytest_configure(config):
    module = importlib.import_module({module_name!r})
    source = Path(module.__file__).read_text(encoding="utf-8")
    guard = {guard!r}
    assert source.count(guard) == 1
    mutated_src = source.replace(guard, {mutated!r})
    mutant = compile(mutated_src, module.__file__, "exec")
    exec(mutant, module.__dict__)
"""


def _run_pytest_node(
    target_test: str, *, plugin_name: str | None = None, plugin_dir: Path | None = None
) -> subprocess.CompletedProcess[str]:
    repo_root = str(Path.cwd())
    command = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", target_test]
    env = dict(os.environ, PYTHONPATH=repo_root, PYTEST_ADDOPTS="", PYTHONIOENCODING="utf-8")
    if plugin_name is not None:
        assert plugin_dir is not None
        command = [*command[:-1], "-p", plugin_name, command[-1]]
        env["PYTHONPATH"] = f"{repo_root}{os.pathsep}{plugin_dir}"
    return subprocess.run(
        command,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=180,
        check=False,
    )


def test_pytest_gate_turns_red_when_replay_strict_gap_check_is_removed(tmp_path: Path) -> None:
    """게이트 적색 재현 — LA-17 strict 모드의 핵심 계약(§9.2, A5: "기대
    open_time 대비 결측이 있으면 예외를 낸다")을 지우면(strict 판정 회귀),
    `test_replay_strict_gap_raises_incomplete`가 green에서 red로 뒤집혀야
    한다 — 이 negative test가 실제로 그 회귀를 잡는다는 증명(I-10)."""
    module = importlib.import_module("src.foundation.market_data.application.replay_candles")
    source = Path(module.__file__).read_text(encoding="utf-8")
    assert source.count(_STRICT_GAP_GUARD) == 1

    target_test = f"{_THIS_TESTFILE}::test_replay_strict_gap_raises_incomplete"
    baseline = _run_pytest_node(target_test)
    assert baseline.returncode == 0, baseline.stdout + baseline.stderr
    assert "1 passed" in baseline.stdout

    plugin_name = "_mutate_replay_strict_gap_check"
    plugin_path = tmp_path / f"{plugin_name}.py"
    plugin_path.write_text(
        _source_mutation_plugin_source(
            "src.foundation.market_data.application.replay_candles",
            _STRICT_GAP_GUARD,
            _STRICT_GAP_MUTATED,
        ),
        encoding="utf-8",
    )

    mutated = _run_pytest_node(target_test, plugin_name=plugin_name, plugin_dir=tmp_path)
    assert mutated.returncode != 0, mutated.stdout + mutated.stderr
    assert "1 passed" not in mutated.stdout
    assert "1 failed" in mutated.stdout
