"""LA-16 ingest_ticks 통합테스트 — 실 DB(TEST_DATABASE_URL).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9.2 LA-16.
DoD(task-842): 정상 배치 저장+배치행+감사 이벤트 1건; trade_id 역행 배치
전량 REJECT(부분 저장 금지); 재수집 멱등(md_tick 중복 없이 배치행만 새로
남음); 감사 실패 주입 → md_tick·md_ingest_batch_tick 전부 롤백.

DEEPEN(task-2988, DEPTH 감사 task-2723 #842): 원 리프가 D1에 머물렀던
사유 두 가지를 이 파일 하단에서 보강한다 — (1) 수치 성능/지연/round-trip
단언 부재(D2 'ALL of' 요건 미충족), (2) 적대적/다중인스턴스 동시성
테스트 부재(기존 재수집 테스트는 항상 순차 `await`). LA-16은 LA 축이라
DEPTH 하한이 D3(ADR-2026-09-09-C) — round-trip 회귀 게이트(D2)와
`asyncio.gather` 동시 경합(D3 multi-instance) 둘 다 추가한다.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import asyncpg
import pytest

from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.market_data.adapters.postgres_batch_repository import PostgresBatchRepository
from src.foundation.market_data.application.ingest_ticks import IngestTicksCommand, ingest_ticks
from src.foundation.market_data.contracts.v1 import TickRecord, Venue, Verdict


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


def _tick(instrument_id: uuid.UUID, trade_id: str, t: datetime, price: str = "100") -> TickRecord:
    return TickRecord(
        venue=Venue.BITGET,
        instrument_id=instrument_id,
        trade_id=trade_id,
        price=Decimal(price),
        quantity=Decimal("1"),
        side="buy",
        traded_at=t,
    )


class _BoomAuditAppender:
    async def append_event_in(self, conn, **kwargs):
        raise RuntimeError("injected audit failure")


@pytest.fixture
def deps(pool):
    return SimpleNamespace(
        pool=pool,
        audit=PostgresAuditEventRepository(pool),
        batches=PostgresBatchRepository(pool),
    )


async def _run(deps, ticks, *, audit=None) -> object:
    cmd = IngestTicksCommand(tenant_id=None, source="test", ticks=ticks, trace_id=uuid.uuid4())
    return await ingest_ticks(cmd, batches=deps.batches, audit=audit or deps.audit, pool=deps.pool)


async def test_ingest_accepts_and_stores_ticks_with_one_audit_event(pool, deps):
    async with pool.acquire() as conn:
        instrument_id = await _instrument_id(conn)
    t0 = datetime.now(timezone.utc).replace(microsecond=0)
    ticks = [
        _tick(instrument_id, "1", t0),
        _tick(instrument_id, "2", t0 + timedelta(seconds=1)),
    ]

    result = await _run(deps, ticks)

    assert result.verdict.verdict == Verdict.ACCEPT
    assert result.verdict.accepted == 2
    async with pool.acquire() as conn:
        stored = await conn.fetchval(
            "SELECT COUNT(*) FROM md_tick WHERE instrument_id = $1", instrument_id
        )
        events = await conn.fetchval(
            "SELECT COUNT(*) FROM foundation_audit_event WHERE aggregate_id = $1", result.batch_id
        )
        batch_rows = await conn.fetchval(
            "SELECT COUNT(*) FROM md_ingest_batch_tick WHERE id = $1", result.batch_id
        )
    assert stored == 2
    assert events == 1
    assert batch_rows == 1


async def test_ingest_rejects_whole_batch_on_trade_id_regression(pool, deps):
    async with pool.acquire() as conn:
        instrument_id = await _instrument_id(conn)
    t0 = datetime.now(timezone.utc).replace(microsecond=0)
    first = [
        _tick(instrument_id, "1", t0),
        _tick(instrument_id, "3", t0 + timedelta(seconds=2)),
    ]
    await _run(deps, first)

    regressive = [
        _tick(instrument_id, "2", t0 + timedelta(seconds=3)),
        _tick(instrument_id, "4", t0 + timedelta(seconds=4)),
    ]
    result = await _run(deps, regressive)

    assert result.verdict.verdict == Verdict.REJECT
    assert result.verdict.rejected == 2
    assert result.verdict.accepted == 0
    async with pool.acquire() as conn:
        stored = await conn.fetchval(
            "SELECT COUNT(*) FROM md_tick WHERE instrument_id = $1 AND trade_id IN ('2', '4')",
            instrument_id,
        )
        outcome = await conn.fetchval(
            "SELECT outcome FROM foundation_audit_event WHERE aggregate_id = $1", result.batch_id
        )
    assert stored == 0, "역행 배치는 부분 저장 없이 전량 REJECT되어야 한다"
    assert outcome == "DENIED"


async def test_ingest_reingest_is_idempotent_and_creates_new_batch_row(pool, deps):
    async with pool.acquire() as conn:
        instrument_id = await _instrument_id(conn)
    t0 = datetime.now(timezone.utc).replace(microsecond=0)
    ticks = [_tick(instrument_id, "1", t0), _tick(instrument_id, "2", t0 + timedelta(seconds=1))]

    first = await _run(deps, ticks)
    second = await _run(deps, ticks)

    assert first.batch_id != second.batch_id
    assert second.verdict.verdict == Verdict.ACCEPT, "같은 배치 재수집은 역행이 아니다"
    async with pool.acquire() as conn:
        tick_count = await conn.fetchval(
            "SELECT COUNT(*) FROM md_tick WHERE instrument_id = $1", instrument_id
        )
        batch_count = await conn.fetchval(
            "SELECT COUNT(*) FROM md_ingest_batch_tick WHERE instrument_id = $1", instrument_id
        )
    assert tick_count == 2, "재수집은 멱등해야 한다(같은 틱이 두 번 저장되지 않음)"
    assert batch_count == 2, "배치 기록 자체는 호출마다 새로 남는다(INSERT only)"


async def test_ingest_rejects_regression_hidden_by_same_timestamp_tie(pool, deps):
    """동시 체결(같은 traded_at)로 저장된 두 trade_id 중 baseline이 더 작은
    쪽으로 뽑히더라도(같은 트랜잭션은 created_at도 동일해 단순 LIMIT 1
    tie-break로는 구분 불가) 그 사이 값 재수집은 여전히 역행으로 잡혀야
    한다(QA 발견, task-1004)."""
    async with pool.acquire() as conn:
        instrument_id = await _instrument_id(conn)
    t0 = datetime.now(timezone.utc).replace(microsecond=0)

    first = [_tick(instrument_id, "5", t0), _tick(instrument_id, "9", t0)]
    r1 = await _run(deps, first)
    assert r1.verdict.verdict == Verdict.ACCEPT

    second = [_tick(instrument_id, "7", t0)]
    r2 = await _run(deps, second)

    assert r2.verdict.verdict == Verdict.REJECT
    async with pool.acquire() as conn:
        stored = await conn.fetchval(
            "SELECT COUNT(*) FROM md_tick WHERE instrument_id = $1 AND trade_id = '7'",
            instrument_id,
        )
    assert stored == 0


async def test_ingest_rejects_regression_when_same_trade_id_reused_at_earlier_time(pool, deps):
    """같은 trade_id를 다른(더 이른) traded_at으로 재수집하면 `md_tick`
    UNIQUE(venue, instrument_id, trade_id, traded_at)와 다른 복합키라
    "재수집"이 아니라 신규 행이다 — trade_id만으로 "이미 안다"고 판정하면
    이 배치가 역행검사를 건너뛰고 통과해버린다(리뷰 REJECT, task-1302).
    올바른 구현은 traded_at 역행으로 배치 전체를 REJECT하고 새 행을
    남기지 않아야 한다."""
    async with pool.acquire() as conn:
        instrument_id = await _instrument_id(conn)
    t0 = datetime.now(timezone.utc).replace(microsecond=0)

    first = [_tick(instrument_id, "1", t0), _tick(instrument_id, "5", t0 + timedelta(seconds=5))]
    r1 = await _run(deps, first)
    assert r1.verdict.verdict == Verdict.ACCEPT

    regressive = [_tick(instrument_id, "5", t0 + timedelta(seconds=2))]
    result = await _run(deps, regressive)

    assert result.verdict.verdict == Verdict.REJECT
    assert result.verdict.rejected == 1
    assert result.verdict.accepted == 0
    async with pool.acquire() as conn:
        row_count = await conn.fetchval(
            "SELECT COUNT(*) FROM md_tick WHERE instrument_id = $1 AND trade_id = '5'",
            instrument_id,
        )
        outcome = await conn.fetchval(
            "SELECT outcome FROM foundation_audit_event WHERE aggregate_id = $1", result.batch_id
        )
    assert row_count == 1, "역행 배치는 새 행을 남기지 않아야 한다(기존 1행만 존재)"
    assert outcome == "DENIED"


async def test_ingest_rolls_back_md_tick_on_audit_failure(pool, deps):
    async with pool.acquire() as conn:
        instrument_id = await _instrument_id(conn)
    t0 = datetime.now(timezone.utc).replace(microsecond=0)
    ticks = [_tick(instrument_id, "1", t0)]

    with pytest.raises(RuntimeError):
        await _run(deps, ticks, audit=_BoomAuditAppender())

    async with pool.acquire() as conn:
        tick_count = await conn.fetchval(
            "SELECT COUNT(*) FROM md_tick WHERE instrument_id = $1", instrument_id
        )
        batch_count = await conn.fetchval(
            "SELECT COUNT(*) FROM md_ingest_batch_tick WHERE instrument_id = $1", instrument_id
        )
    assert tick_count == 0
    assert batch_count == 0


class _QueryCountingPool:
    """`ingest_ticks`는 `pool`을 duck-type으로만 쓴다(`.acquire()`만 호출,
    ingest_ticks.py 198행) — 실제 `asyncpg.Pool` 대신 이 래퍼를 넘겨 그
    안에서 내주는 커넥션 하나에 쿼리 로거를 심는다. `test_ingest_candles.py`
    `_QueryCountingPool`(task-2976)과 동일한 결정."""

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


_MAX_INGEST_TICKS_ROUND_TRIPS = 12


@pytest.mark.perf
async def test_ingest_round_trip_count_regression_guard(pool, deps):
    """수치 성능 단언(DoD, D2 'ALL of' 요건) — `test_ingest_round_trip_count_
    regression_guard`(task-2976, LA-15 ingest_candles)와 동일 결정: 공유 로컬
    Postgres의 절대 지연시간은 이 파일이 통제할 수 없는 변동성이 이미 여러
    차례(task-920/1029/1038) 드러났으므로 게이트로 쓰지 않고, 호출당 순차
    DB 왕복 수(구조 회귀 — 배치 크기에 비례해 늘어나는 N+1 조회가 생기면
    잡아낸다)만 차단 게이트로 잡는다. 20개 틱을 넣어도 왕복 수는 advisory
    lock + _known_ticks + _last_stored(2) + audit(3) + create_tick_batch +
    _store_ticks의 고정 7회 부근이어야 한다(executemany는 배치 크기와 무관
    하게 1왕복)."""
    async with pool.acquire() as conn:
        instrument_id = await _instrument_id(conn)
    t0 = datetime.now(timezone.utc).replace(microsecond=0)
    ticks = [_tick(instrument_id, str(i), t0 + timedelta(seconds=i)) for i in range(20)]
    queries: list[str] = []
    counting_deps = SimpleNamespace(
        pool=_QueryCountingPool(pool, queries), audit=deps.audit, batches=deps.batches
    )

    result = await _run(counting_deps, ticks)

    assert result.verdict.verdict == Verdict.ACCEPT
    print(
        f"[LA-16 ingest_ticks] sequential DB round trips={len(queries)} "
        f"(max={_MAX_INGEST_TICKS_ROUND_TRIPS})"
    )
    assert len(queries) <= _MAX_INGEST_TICKS_ROUND_TRIPS, (
        f"ingest_ticks 순차 DB 왕복 수({len(queries)})가 상한"
        f"({_MAX_INGEST_TICKS_ROUND_TRIPS})을 초과했습니다 — 틱 개수와 무관하게"
        " 왕복 수는 일정해야 하는데(배치 단위 executemany), 왕복 수 회귀입니다."
    )


async def test_concurrent_conflicting_batches_serialize_via_advisory_lock(pool, deps):
    """적대적/다중인스턴스 동시성 테스트(DoD, D3) — 기존 재수집 테스트는
    항상 `await`로 순차 실행돼 advisory lock의 실제 목적(모듈 docstring
    §"동시 배치의 '직전 저장분' 경쟁 방지")을 한 번도 실동시성으로
    증명하지 못했다.

    같은 (venue, instrument_id)에 서로 모순되는 두 배치를 `asyncio.gather`
    로 동시에 던진다 — 배치 A(trade_id 11~15, t0+10s~t0+14s)와 배치 B
    (trade_id 12 하나, t0+11s, A의 구간 한가운데). lock이 없으면 두
    트랜잭션이 커밋 전에 서로의 변경을 못 보고 둘 다 통과할 수 있다(각자
    커밋 *이전*의 baseline만 보고 서로를 모른다) — 그 경우 최종 상태에
    trade_id 역행이 남는다(§9 DoD 위반). lock이 트랜잭션을 직렬화하면
    나중에 실행되는 쪽은 먼저 커밋된 쪽의 baseline을 다시 읽으므로, 실행
    순서와 무관하게 둘 중 정확히 하나만 ACCEPT되고 최종 데이터는 항상
    traded_at 오름차순으로 trade_id도 단조 증가해야 한다.

    동시에 다른 instrument_id(별도 advisory lock 키)로도 한 배치를 같이
    쏘아 서로 다른 인스트루먼트의 동시 인제스트가 격리되는지(교차 오염
    없음)도 같은 `asyncio.gather` 호출에서 함께 증명한다."""
    async with pool.acquire() as conn:
        instrument_id = await _instrument_id(conn)
        other_instrument_id = await _instrument_id(conn)
    t0 = datetime.now(timezone.utc).replace(microsecond=0)

    baseline = [_tick(instrument_id, str(i), t0 + timedelta(seconds=i)) for i in range(1, 11)]
    baseline_result = await _run(deps, baseline)
    assert baseline_result.verdict.verdict == Verdict.ACCEPT

    batch_a = [_tick(instrument_id, str(i), t0 + timedelta(seconds=i)) for i in range(11, 16)]
    # A의 trade_id "12"(t0+12s)와 타임스탬프가 겹치지 않게 11.5초로 둔다 —
    # 동시 틱(같은 traded_at) tie-break는 비결정적이라(모듈 docstring
    # task-1004 참고) 최종 정렬 순서로 오염 여부를 판정할 때 애매해진다.
    batch_b = [_tick(instrument_id, "12", t0 + timedelta(seconds=11, milliseconds=500))]
    other = [_tick(other_instrument_id, "1", t0)]

    result_a, result_b, result_other = await asyncio.gather(
        _run(deps, batch_a),
        _run(deps, batch_b),
        _run(deps, other),
    )

    verdicts = {result_a.verdict.verdict, result_b.verdict.verdict}
    assert verdicts == {Verdict.ACCEPT, Verdict.REJECT}, (
        "경쟁하는 두 배치 중 정확히 하나만 ACCEPT되어야 한다(lock이 직렬화"
        f" 실패) — 실제: A={result_a.verdict.verdict}, B={result_b.verdict.verdict}"
    )
    assert result_other.verdict.verdict == Verdict.ACCEPT, (
        "다른 인스트루먼트는 경쟁과 무관하게 성공해야 한다"
    )

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT trade_id, traded_at FROM md_tick WHERE instrument_id = $1 ORDER BY traded_at",
            instrument_id,
        )
    trade_ids = [int(row["trade_id"]) for row in rows]
    assert trade_ids == sorted(trade_ids), (
        "동시 경쟁 후에도 저장된 틱은 traded_at 오름차순으로 trade_id가 단조"
        f" 증가해야 한다(역행 없음) — 실제 순서: {trade_ids}"
    )
    assert len(trade_ids) == len(set(trade_ids)), (
        "trade_id '12'가 두 번(배치 A와 B 양쪽에서) 저장되면 안 된다 — lock이"
        f" 깨져 둘 다 ACCEPT된 오염이다 — 실제 저장분: {trade_ids}"
    )
