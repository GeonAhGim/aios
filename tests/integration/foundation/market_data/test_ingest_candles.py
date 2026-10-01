"""LA-15 ingest_candles/bitget_ingest_source 통합테스트 — 실 DB(TEST_DATABASE_URL) +
httpx.MockTransport(실키 금지).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§8.2, §9.2 LA-15.
DoD(task-623): 정상 배치 저장+배치행+감사 이벤트 1건; 재실행 저장 0·새 배치행;
DUPLICATE_CONFLICT 격리(기존 불변); OHLC 위반 캔들만 격리(나머지 정상 저장);
감사 실패 주입 → md_ingest_batch/md_candle/md_quarantine_candle/md_quality_issue
전부 롤백; bitget_ingest_source는 MockTransport로만 검증.

task-10465 F1(M) tenant_id 전파 테스트는 loc_over_500 분할로
test_ingest_candles_tenant.py에 있다(공용 헬퍼는 ingest_candles_support.py).
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from src.exchanges.bitget.adapter import BitgetAdapter
from src.foundation.market_data.adapters.bitget_ingest_source import BitgetIngestSource
from src.foundation.market_data.application.ingest_candles import ingest_candles
from src.foundation.market_data.contracts.v1 import (
    Timeframe,
    Venue,
    Verdict,
)
from tests.integration.foundation.market_data.ingest_candles_support import (
    _BoomAuditAppender,
    _candle,
    _clock,
    _cmd,
    _FakeIngestSource,
    _listed_instrument,
    _run,
    deps,  # noqa: F401 -- pytest fixture, discovered via import
)

__all__ = ["deps"]


async def test_ingest_accepts_and_stores_batch_with_one_audit_event(pool, deps):
    instrument = await _listed_instrument(deps)
    t0 = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    candles = [
        _candle(t0, "100", "110", "90", "105", "10"),
        _candle(t0 + timedelta(minutes=1), "105", "115", "95", "110", "12"),
    ]
    result = await _run(
        deps,
        _cmd(instrument, t0, t0 + timedelta(minutes=2)),
        _FakeIngestSource(candles),
        clock_at=t0,
    )

    assert result.verdict.verdict == Verdict.ACCEPT
    assert result.verdict.accepted == 2
    async with pool.acquire() as conn:
        stored = await conn.fetchval(
            "SELECT COUNT(*) FROM md_candle WHERE batch_id = $1", result.batch_id
        )
        events = await conn.fetchval(
            "SELECT COUNT(*) FROM foundation_audit_event WHERE aggregate_id = $1", result.batch_id
        )
        batch_rows = await conn.fetchval(
            "SELECT COUNT(*) FROM md_ingest_batch WHERE id = $1", result.batch_id
        )
    assert stored == 2
    assert events == 1
    assert batch_rows == 1


async def test_ingest_reingest_is_idempotent_and_creates_new_batch_row(pool, deps):
    instrument = await _listed_instrument(deps)
    t0 = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    candles = [_candle(t0, "100", "110", "90", "105", "10")]
    cmd = _cmd(instrument, t0, t0 + timedelta(minutes=1))

    first = await _run(deps, cmd, _FakeIngestSource(candles), clock_at=t0)
    second = await _run(deps, cmd, _FakeIngestSource(candles), clock_at=t0)

    assert first.batch_id != second.batch_id
    async with pool.acquire() as conn:
        candle_count = await conn.fetchval(
            "SELECT COUNT(*) FROM md_candle WHERE instrument_id = $1", instrument.instrument_id
        )
        batch_count = await conn.fetchval(
            "SELECT COUNT(*) FROM md_ingest_batch WHERE instrument_id = $1",
            instrument.instrument_id,
        )
    assert candle_count == 1, "재수집은 멱등해야 한다(같은 캔들이 두 번 저장되지 않음)"
    assert batch_count == 2, "배치 기록 자체는 호출마다 새로 남는다(md_ingest_batch는 INSERT only)"


async def test_ingest_quarantines_duplicate_conflict_candles(pool, deps):
    instrument = await _listed_instrument(deps)
    t0 = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    conflicting = [
        _candle(t0, "100", "110", "90", "105", "10"),
        _candle(t0, "100", "120", "90", "115", "10"),  # 같은 open_time, 내용 다름
    ]
    result = await _run(
        deps,
        _cmd(instrument, t0, t0 + timedelta(minutes=1)),
        _FakeIngestSource(conflicting),
        clock_at=t0,
    )

    async with pool.acquire() as conn:
        stored = await conn.fetchval(
            "SELECT COUNT(*) FROM md_candle WHERE instrument_id = $1 AND open_time = $2",
            instrument.instrument_id,
            t0,
        )
        quarantined = await conn.fetchval(
            "SELECT COUNT(*) FROM md_quarantine_candle "
            "WHERE batch_id = $1 AND issue_type = 'DUPLICATE_CONFLICT'",
            result.batch_id,
        )
    assert stored == 0, "충돌 캔들은 정상 테이블에 남지 않는다(기존 불변)"
    assert quarantined >= 1


async def test_ingest_cross_batch_conflict_corrects_accepted_and_audits_it(pool, deps):
    """[health:audit_correction] task-10568 F8(M) — AUDIT_2026-10-01_data_ingest_replay.md
    §4 F8. 같은 (venue, instrument_id, timeframe, open_time)을 두 ingest 호출이 건드리면
    (백필·실시간 경쟁 상황 재현), 늦게 들어오는 호출은 `upsert_batch`의
    `ON CONFLICT DO NOTHING`에 의해 실제로는 0행 삽입이다. 수정 전에는 이 반환값이
    버려져 `IngestBatchResult.verdict.accepted`가 자기 배치의 품질 판정(1)만 보고
    ACCEPT/accepted=1로 기록됐다 — 실제 저장 결과(0행)와 감사 기록이 어긋났다(negative
    재현). 수정 후에는 `accepted`가 실제 삽입 행수(0)와 일치해야 하고, 그 불일치를
    별도의 추가 감사 이벤트(`market_data.candles_ingest_conflict`)로 사실대로 남겨야
    한다(원래 이벤트는 WORM이라 그대로 두고, 추가 이벤트로 진실을 보강한다).
    """
    instrument = await _listed_instrument(deps)
    t0 = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    candle = _candle(t0, "100", "110", "90", "105", "10")
    cmd = _cmd(instrument, t0, t0 + timedelta(minutes=1))

    first = await _run(deps, cmd, _FakeIngestSource([candle]), clock_at=t0)
    assert first.verdict.verdict == Verdict.ACCEPT
    assert first.verdict.accepted == 1

    second = await _run(deps, cmd, _FakeIngestSource([candle]), clock_at=t0)

    assert second.batch_id != first.batch_id
    assert second.verdict.accepted == 0, (
        "두 번째 호출의 캔들은 자기 배치 품질 판정만 보면 ACCEPT·accepted=1이지만, "
        "DB에는 첫 번째 배치가 이미 같은 키로 저장해 둔 행이 있어 실제 삽입은 0행이다 "
        "-- 보고되는 accepted는 실제 삽입 결과와 일치해야 한다"
    )
    async with pool.acquire() as conn:
        stored_for_second_batch = await conn.fetchval(
            "SELECT COUNT(*) FROM md_candle WHERE batch_id = $1", second.batch_id
        )
        conflict_events = await conn.fetchval(
            "SELECT COUNT(*) FROM foundation_audit_event "
            "WHERE aggregate_id = $1 AND action = 'market_data.candles_ingest_conflict'",
            second.batch_id,
        )
        original_events = await conn.fetchval(
            "SELECT COUNT(*) FROM foundation_audit_event "
            "WHERE aggregate_id = $1 AND action = 'market_data.candles_ingested'",
            second.batch_id,
        )
    assert stored_for_second_batch == 0
    assert conflict_events == 1, (
        "실제 저장 결과와 품질 판정이 어긋나면 보강 감사 이벤트가 남아야 한다"
    )
    assert original_events == 1, "원래 WORM 감사 이벤트는 그대로 유지된다(사후 수정 아님)"


async def test_ingest_quarantines_ohlc_violation_candle_and_stores_rest(pool, deps):
    instrument = await _listed_instrument(deps)
    t0 = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    good = [_candle(t0 + timedelta(minutes=i), "100", "110", "90", "105", "10") for i in range(4)]
    bad = _candle(t0 + timedelta(minutes=4), "100", "90", "80", "85", "10")  # high(90) < open(100)
    result = await _run(
        deps,
        _cmd(instrument, t0, t0 + timedelta(minutes=5)),
        _FakeIngestSource([*good, bad]),
        clock_at=t0,
    )

    assert result.verdict.verdict == Verdict.PARTIAL
    async with pool.acquire() as conn:
        stored = await conn.fetchval(
            "SELECT COUNT(*) FROM md_candle WHERE batch_id = $1", result.batch_id
        )
        quarantined = await conn.fetchval(
            "SELECT COUNT(*) FROM md_quarantine_candle "
            "WHERE batch_id = $1 AND issue_type = 'OHLC_INCONSISTENT'",
            result.batch_id,
        )
    assert stored == 4
    assert quarantined == 1


async def test_ingest_rolls_back_everything_on_audit_failure(pool, deps):
    instrument = await _listed_instrument(deps)
    t0 = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    candles = [_candle(t0, "100", "110", "90", "105", "10")]
    cmd = _cmd(instrument, t0, t0 + timedelta(minutes=1))

    with pytest.raises(RuntimeError):
        await _run(deps, cmd, _FakeIngestSource(candles), audit=_BoomAuditAppender(), clock_at=t0)

    async with pool.acquire() as conn:
        candle_count = await conn.fetchval(
            "SELECT COUNT(*) FROM md_candle WHERE instrument_id = $1", instrument.instrument_id
        )
        batch_count = await conn.fetchval(
            "SELECT COUNT(*) FROM md_ingest_batch WHERE instrument_id = $1",
            instrument.instrument_id,
        )
        issue_count = await conn.fetchval(
            "SELECT COUNT(*) FROM md_quality_issue WHERE batch_id IN "
            "(SELECT id FROM md_ingest_batch WHERE instrument_id = $1)",
            instrument.instrument_id,
        )
    assert candle_count == 0
    assert batch_count == 0
    assert issue_count == 0


# --- DEEPEN task-2976 — 수치 성능 단언 + 게이트 적색 재현. DEPTH 감사
# (task-2723, docs/audit/DEPTH_LA_LB_LC.md #623)가 D1로 판정한 증빙 공백을
# 메운다. failure-injection과 negative는 위 5개 테스트로 이미 충족됨. ---


class _QueryCountingPool:
    """`ingest_candles`는 `post_refund`(task-2962)와 달리 호출자 `conn`을
    받지 않고 자기 `pool`에서 직접 두 번 acquire한다(읽기 전용 조회 1회 +
    쓰기 트랜잭션 1회, ingest_candles.py 176행/224행) — 그래서 하나의
    `conn.add_query_logger`로는 못 재고, `pool.acquire()`가 내주는 커넥션
    각각에 로거를 심어야 한다. `ingest_candles`는 `pool`을 duck-type으로만
    쓰므로(`.acquire()`만 호출) 실제 asyncpg.Pool 대신 이 래퍼를 넘긴다."""

    def __init__(self, pool: object, queries: list[str]) -> None:
        self._pool = pool
        self._queries = queries

    def acquire(self) -> _QueryCountingPool._AcquireCtx:
        return self._AcquireCtx(self._pool, self._queries)

    class _AcquireCtx:
        def __init__(self, pool: object, queries: list[str]) -> None:
            self._pool = pool
            self._queries = queries
            self._ctx: object = None

        async def __aenter__(self):
            self._ctx = self._pool.acquire()
            conn = await self._ctx.__aenter__()
            conn.add_query_logger(lambda record: self._queries.append(getattr(record, "query", "")))
            return conn

        async def __aexit__(self, *exc: object) -> object:
            return await self._ctx.__aexit__(*exc)


_MAX_INGEST_CANDLES_ROUND_TRIPS = 22


@pytest.mark.perf
async def test_ingest_round_trip_count_regression_guard(pool, deps):
    """수치 성능 단언(DoD) — `test_refund_r1_round_trip_count_regression_guard`
    (task-2962)와 동일 결정을 따른다: 공유 로컬 Postgres에서 절대 지연시간은
    이 파일이 통제할 수 없는 변동성이 이미 여러 차례(task-920/1029/1038)
    드러났으므로 게이트로 쓰지 않고, 호출당 순차 DB 왕복 수(구조 회귀 —
    N+1 조회가 다시 생기는 실제 코드 결함)만 차단 게이트로 잡는다."""
    instrument = await _listed_instrument(deps)
    t0 = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    candles = [
        _candle(t0 + timedelta(minutes=i), "100", "110", "90", "105", "10") for i in range(20)
    ]
    queries: list[str] = []

    result = await ingest_candles(
        _cmd(instrument, t0, t0 + timedelta(minutes=20)),
        source=_FakeIngestSource(candles),
        store=deps.store,
        refs=deps.refs,
        cal=deps.cal,
        batches=deps.batches,
        audit=deps.audit,
        pool=_QueryCountingPool(pool, queries),
        clock=_clock(t0),
    )

    assert result.verdict.verdict == Verdict.ACCEPT
    print(
        f"[LA-15 ingest_candles] sequential DB round trips={len(queries)} "
        f"(max={_MAX_INGEST_CANDLES_ROUND_TRIPS})"
    )
    assert len(queries) <= _MAX_INGEST_CANDLES_ROUND_TRIPS, (
        f"ingest_candles 순차 DB 왕복 수({len(queries)})가 상한"
        f"({_MAX_INGEST_CANDLES_ROUND_TRIPS})을 초과했습니다 — 캔들 개수와 무관하게"
        " 왕복 수는 일정해야 하는데(배치 단위 upsert/quarantine), 왕복 수 회귀입니다."
    )


async def test_ingest_quarantines_entire_batch_when_reject_ratio_gate_turns_red(pool, deps):
    """게이트 적색 재현(DoD) — §4.1 "배치의 REJECT 비율 > 20% → 배치 전체
    QUARANTINE(부분 저장 금지)"(`verdict.decide`의 `_REJECT_RATIO_LIMIT`)는
    이미 구현되어 있으나 이 파일에는 QUARANTINE 판정 자체가 실측된 적이
    없었다(기존 테스트는 ACCEPT/PARTIAL만 다룸). OHLC 위반 비율을 40%
    (2/5, 상한 20% 초과)로 만들어 게이트를 적색으로 뒤집고 "좋은" 캔들까지
    포함한 배치 전체가 저장 대신 격리되는지 증명한다."""
    instrument = await _listed_instrument(deps)
    t0 = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    good = [_candle(t0 + timedelta(minutes=i), "100", "110", "90", "105", "10") for i in range(3)]
    # high(90) < open(100) — test_ingest_quarantines_ohlc_violation... 와 동일한
    # 캔들로, check_candle이 REJECT 이슈를 정확히 1개만 내는 것이 이미 검증됨.
    bad = [
        _candle(t0 + timedelta(minutes=3), "100", "90", "80", "85", "10"),
        _candle(t0 + timedelta(minutes=4), "100", "90", "80", "85", "10"),
    ]
    result = await _run(
        deps,
        _cmd(instrument, t0, t0 + timedelta(minutes=5)),
        _FakeIngestSource([*good, *bad]),
        clock_at=t0,
    )

    assert result.verdict.verdict == Verdict.QUARANTINE
    assert result.verdict.accepted == 0
    async with pool.acquire() as conn:
        stored = await conn.fetchval(
            "SELECT COUNT(*) FROM md_candle WHERE batch_id = $1", result.batch_id
        )
        quarantined = await conn.fetchval(
            "SELECT COUNT(*) FROM md_quarantine_candle WHERE batch_id = $1", result.batch_id
        )
    assert stored == 0, "REJECT 비율 초과 시 부분 저장 금지 — 좋은 캔들도 저장되지 않는다"
    assert quarantined == 5, "배치 전체(좋은 캔들 포함)가 격리된다"


async def test_bitget_ingest_source_fetches_and_filters_via_mock_transport():
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    ts_ms = int(t0.timestamp() * 1000)
    rows = [
        [str(ts_ms), "100", "110", "90", "105", "10", "1000", "1000"],
        [str(ts_ms + 60_000), "105", "115", "95", "110", "12", "1200", "1200"],
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/spot/market/candles"
        assert request.url.params.get("symbol") == "BTCUSDT"
        return httpx.Response(200, json={"code": "00000", "msg": "success", "data": rows})

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(base_url="https://api.bitget.com", transport=transport)
    adapter = BitgetAdapter("key", "secret", "passphrase", demo_mode=True, http_client=client)
    source = BitgetIngestSource(adapter)

    result = await source.fetch_candles(
        Venue.BITGET, "BTCUSDT", Timeframe.M1, t0, t0 + timedelta(minutes=1)
    )

    assert len(result) == 1
    assert result[0].open_time == t0
    assert result[0].close == Decimal("105")


def test_batch_hash_is_stable_across_different_pythonhashseed() -> None:
    """[health:audit_correction] task-10471 — AUDIT_2026-10-01_data_ingest_replay.md §2 F7(L).

    The audit claims `ingest_candles.py` builds `batch_hash` with the builtin
    `hash()`, which is `PYTHONHASHSEED`-randomized per process and would break
    dedup. The current call site (`ingest_candles.py` -> `batch_hash(rekeyed)`)
    actually delegates to `domain/lineage.py::batch_hash`, which is
    `hashlib.sha256`-based and not seed-dependent — this test proves the value
    `ingest_candles` persists as `md_ingest_batch.batch_hash` is identical
    across two processes with different `PYTHONHASHSEED`, i.e. the audited
    failure mode does not reproduce against the code as it stands.
    """
    script = (
        "from decimal import Decimal\n"
        "from datetime import datetime, timedelta, timezone\n"
        "import uuid\n"
        "from src.foundation.market_data.contracts.v1 import (\n"
        "    CandleRecord, SeriesKey, Timeframe, Venue,\n"
        ")\n"
        "from src.foundation.market_data.domain.lineage import batch_hash\n"
        "instrument_id = uuid.UUID('11111111-1111-1111-1111-111111111111')\n"
        "t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)\n"
        "records = [\n"
        "    CandleRecord(\n"
        "        key=SeriesKey(\n"
        "            venue=Venue.BITGET, instrument_id=instrument_id, timeframe=Timeframe.M1,\n"
        "        ),\n"
        "        open_time=t0 + timedelta(minutes=i),\n"
        "        close_time=t0 + timedelta(minutes=i + 1),\n"
        "        open=Decimal('100'), high=Decimal('110'), low=Decimal('90'),\n"
        "        close=Decimal('105'), volume=Decimal('10'),\n"
        "    )\n"
        "    for i in range(3)\n"
        "]\n"
        "print(batch_hash(records))\n"
    )

    def _run_with_seed(seed: str) -> str:
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=str(Path(__file__).resolve().parents[4]),
            env={**os.environ, "PYTHONHASHSEED": seed},
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert completed.returncode == 0, completed.stderr
        return completed.stdout.strip()

    digest_seed_0 = _run_with_seed("0")
    digest_seed_1 = _run_with_seed("1")
    digest_seed_random = _run_with_seed("random")

    assert digest_seed_0 == digest_seed_1 == digest_seed_random, (
        "batch_hash must be identical regardless of PYTHONHASHSEED — "
        f"got {digest_seed_0!r}, {digest_seed_1!r}, {digest_seed_random!r}"
    )
