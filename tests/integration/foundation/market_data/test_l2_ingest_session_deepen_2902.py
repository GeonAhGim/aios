"""RD-19 `L2IngestSession` x 실 DB(`coverage_spans`) — DEEPEN(task-2902,
docs/audit/DEPTH_DC_RD.md#1766) D0/D1 -> D3 증빙.

기존 `test_l2_ingest_session_coverage.py`는 단일 인스턴스가 실 DB
EXCLUDE 제약을 통과해 갭 결손 span 2개를 남기는 정상 경로만 증명했다
(D1). 이 파일이 부족분을 채운다: (1) 이미 겹치는 span이 존재하는 상태에서
세션이 재동기화 후 남기려는 span이 실 DB `EXCLUDE USING gist` 제약을
위반하면 `CoverageSpanOverlapError`가 조용히 삼켜지지 않고 세션 실행
전체를 표면화하는 실패주입/게이트 적색 재현, (2) 서로 다른 두 종목에 대한
`L2IngestSession` 두 인스턴스를 실 DB에 대해 진짜 동시(asyncio.gather)로
실행해도 각 종목의 span이 서로 오염되지 않는 D3 동시 다중 인스턴스 증명,
(3) 반복되는 갭/재동기화 사이클이 실 DB 쓰기 경로에서 절대시간 성능 예산을
지키는지. `l2_ingest_session.py`/`postgres_coverage_repository.py`/
마이그레이션은 무수정 — 새 기능 없음, 깊이만 올린다.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import asyncpg
import pytest
from websockets.exceptions import ConnectionClosed

from src.exchanges.common.ws_session import NOT_ACK
from src.foundation.market_data.adapters.ingest.l2_ingest_session import L2IngestSession
from src.foundation.market_data.adapters.postgres_coverage_repository import (
    CoverageSpanOverlapError,
    PostgresCoverageRepository,
)
from src.foundation.market_data.contracts.v1 import Timeframe, Venue
from src.foundation.market_data.domain.l2_orderbook import L2Diff, L2Snapshot
from src.foundation.market_data.ports.coverage_repository import CoverageQuality, CoverageSpan


class _Stop(Exception):
    pass


class _FakeConnection:
    def __init__(self, messages: list[str], *, raise_after: BaseException | None = None) -> None:
        self._messages = messages
        self._raise_after = raise_after

    async def send(self, message: str) -> None:
        return None

    def __aiter__(self):
        return self._iter()

    async def _iter(self):
        for message in self._messages:
            yield message
        if self._raise_after is not None:
            raise self._raise_after


class _Ctx:
    def __init__(self, connection: _FakeConnection) -> None:
        self._connection = connection

    async def __aenter__(self):
        return self._connection

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _connect_sequence(connections: list[_FakeConnection]):
    calls = {"n": 0}

    def connect_fn(url: str):
        calls["n"] += 1
        if calls["n"] <= len(connections):
            return _Ctx(connections[calls["n"] - 1])
        raise _Stop

    return connect_fn


async def _no_sleep(_: float) -> None:
    return None


async def _never(_: float) -> None:
    await asyncio.Event().wait()


class _TickingClock:
    def __init__(self, start: datetime) -> None:
        self._now = start

    def __call__(self) -> datetime:
        current = self._now
        self._now = self._now + timedelta(seconds=1)
        return current


class _FakeBinanceAdapter:
    venue = Venue.BINANCE

    def ws_url(self, instrument_symbol: str) -> str:
        return "wss://fake"

    def subscription_messages(self, instrument_symbol: str) -> list[dict[str, Any]]:
        return [{"op": "subscribe"}]

    def ack_validator(self, message: dict[str, Any]):
        return NOT_ACK

    def seq_extractor(self, message: dict[str, Any]) -> int | None:
        value = message.get("seq")
        return int(value) if value is not None else None

    def parse_event(self, message: dict[str, Any]) -> L2Diff | None:
        return L2Diff(
            sequence=int(message["seq"]),
            as_of=datetime.now(timezone.utc),
            bid_updates=(),
            ask_updates=(),
        )

    async def fetch_snapshot(self, instrument_symbol: str) -> L2Snapshot:
        return L2Snapshot(sequence=0, as_of=datetime.now(timezone.utc), bids={}, asks={})


def _fake_ulid() -> str:
    return "0" + uuid.uuid4().hex[:25].upper()


def _frame(seq: int) -> str:
    return json.dumps({"seq": seq})


@pytest.fixture(autouse=True)
async def _cleanup_l2_coverage_rows(pool: asyncpg.Pool):
    """이 파일이 남기는 `Timeframe.L2` 행을 정리한다 —
    `test_l2_ingest_session_coverage.py`와 동일 위생 규칙(마이그레이션
    4b19195124bb downgrade round-trip 오염 방지)."""
    yield
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM coverage_spans WHERE timeframe = 'L2'")


async def _insert_instrument(pool: asyncpg.Pool, instrument_id: str) -> None:
    await pool.execute(
        """
        INSERT INTO instruments (
            instrument_id, asset_class, base, quote, isin, figi,
            tick_size, lot_size, calendar_id, lifecycle_state
        ) VALUES ($1, 'CRYPTO', 'BTC', 'USDT', NULL, NULL, 0.01, 0.0001, '24x7', 'ACTIVE')
        """,
        instrument_id,
    )


def _session(instrument_id: str, pool, repo, clock, connect_fn) -> L2IngestSession:
    return L2IngestSession(
        adapter=_FakeBinanceAdapter(),
        instrument_id=instrument_id,
        instrument_symbol="BTCUSDT",
        pool=pool,
        coverage_repo=repo,
        clock=clock,
        ws_session_kwargs={
            "connect_fn": connect_fn,
            "sleep_fn": _no_sleep,
            "ping_sleep_fn": _never,
        },
    )


# ---------------------------------------------------------------------------
# 1. 실패주입 + 게이트 적색 재현 — 실 DB EXCLUDE 위반이 조용히 삼켜지지 않는다
# ---------------------------------------------------------------------------


async def test_real_db_overlap_violation_propagates_out_of_session_run(pool: asyncpg.Pool):
    """다른 워커/재처리 실수로 같은 종목에 이미 겹치는 span이 존재하는
    상태에서 세션이 갭 결손 span을 기록하려 하면, 실 DB `EXCLUDE USING
    gist` 제약이 이를 거부하고 `CoverageSpanOverlapError`가 `_close_span`의
    `logger.exception(...); raise`를 거쳐 `session.run()` 밖까지 그대로
    표면화돼야 한다 — 겹치는 결손 구간을 조용히 버리고 계속 진행하지
    않는다(§4.1 fail-closed)."""
    instrument_id = _fake_ulid()
    await _insert_instrument(pool, instrument_id)
    repo = PostgresCoverageRepository(pool)

    # 세션이 열려는 span과 정확히 겹치도록, 미리 넓은 기존 span을 심어 둔다.
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    async with pool.acquire() as conn, conn.transaction():
        await repo.upsert_span(
            conn,
            CoverageSpan(
                instrument_id=instrument_id,
                venue=Venue.BINANCE,
                timeframe=Timeframe.L2,
                quality=CoverageQuality.PROVISIONAL,
                start=t0 - timedelta(seconds=10),
                end=t0 + timedelta(seconds=10),
            ),
        )

    clock = _TickingClock(t0)  # 세션의 첫 span도 t0 부근에서 시작 -> 기존 span과 겹친다
    frames = [_frame(s) for s in (1, 2, 4, 5)]  # 3 누락 -> 갭 -> close_span 트리거
    conn = _FakeConnection(frames, raise_after=ConnectionClosed(None, None))
    connect_fn = _connect_sequence([conn])

    session = _session(instrument_id, pool, repo, clock, connect_fn)

    with pytest.raises(CoverageSpanOverlapError):
        await session.run()

    async with pool.acquire() as db_conn, db_conn.transaction():
        spans = await repo.list_spans(db_conn, instrument_id, Timeframe.L2)
    assert len(spans) == 1  # 세션이 기록하려던 겹치는 span은 거부되어 저장되지 않았다


# ---------------------------------------------------------------------------
# 2. 동시 다중 인스턴스(D3) — 실 DB에 대해 진짜 동시 실행
# ---------------------------------------------------------------------------


async def test_two_concurrent_sessions_on_different_instruments_do_not_cross_contaminate(
    pool: asyncpg.Pool,
):
    """서로 다른 두 종목을 각각 담당하는 `L2IngestSession` 두 개를 실
    DB에 대해 `asyncio.gather`로 진짜 동시에 돌린다(다중 인스턴스/워커
    시뮬레이션) — 각 종목의 갭 결손 span이 서로 섞이지 않고, 각자 자기
    `instrument_id` 아래에만 (갭 횟수+1)개씩 정확히 기록돼야 한다."""
    instrument_a = _fake_ulid()
    instrument_b = _fake_ulid()
    await _insert_instrument(pool, instrument_a)
    await _insert_instrument(pool, instrument_b)
    repo = PostgresCoverageRepository(pool)

    clock_a = _TickingClock(datetime(2026, 2, 1, tzinfo=timezone.utc))
    clock_b = _TickingClock(datetime(2026, 3, 1, tzinfo=timezone.utc))  # 겹치지 않는 시간대
    frames_a = [_frame(s) for s in (1, 2, 4, 5)]  # 갭 1회
    frames_b = [_frame(s) for s in (1, 3, 4, 6)]  # 갭 2회(2, 5 누락)
    conn_a = _FakeConnection(frames_a, raise_after=ConnectionClosed(None, None))
    conn_b = _FakeConnection(frames_b, raise_after=ConnectionClosed(None, None))

    session_a = _session(instrument_a, pool, repo, clock_a, _connect_sequence([conn_a]))
    session_b = _session(instrument_b, pool, repo, clock_b, _connect_sequence([conn_b]))

    results = await asyncio.gather(session_a.run(), session_b.run(), return_exceptions=True)
    assert all(isinstance(r, _Stop) for r in results)

    async with pool.acquire() as db_conn, db_conn.transaction():
        spans_a = await repo.list_spans(db_conn, instrument_a, Timeframe.L2)
        spans_b = await repo.list_spans(db_conn, instrument_b, Timeframe.L2)

    assert len(spans_a) == 2  # 갭 1회 -> span 2개(갭 전/재동기화 후~끊김)
    assert len(spans_b) == 3  # 갭 2회 -> span 3개(갭 전/갭 사이/재동기화 후~끊김)
    assert all(s.instrument_id == instrument_a for s in spans_a)
    assert all(s.instrument_id == instrument_b for s in spans_b)  # 서로 오염 없음


# ---------------------------------------------------------------------------
# 3. 성능 단언 — 실 DB 경유 반복 갭/재동기화 쓰기
# ---------------------------------------------------------------------------


async def test_repeated_gap_cycles_meet_real_db_write_latency_budget(pool: asyncpg.Pool):
    """실 DB에 대한 반복 갭/재동기화 사이클(span 쓰기 포함)이 절대시간
    예산 내여야 한다 — 24시간 연속 수집에서 갭이 잦아도 DB 쓰기가 병목이
    되지 않는지 증명한다."""
    instrument_id = _fake_ulid()
    await _insert_instrument(pool, instrument_id)
    repo = PostgresCoverageRepository(pool)
    clock = _TickingClock(datetime(2026, 4, 1, tzinfo=timezone.utc))

    n_gaps = 30
    budget_sec = 10.0  # 실측 로컬 <2s, CI/네트워크 편차 감안
    seqs: list[int] = []
    seq = 1
    for _ in range(n_gaps):
        seqs.extend([seq, seq + 1])
        seq += 3  # 매 사이클 1칸씩 갭
    frames = [_frame(s) for s in seqs]
    conn = _FakeConnection(frames, raise_after=ConnectionClosed(None, None))
    connect_fn = _connect_sequence([conn])
    session = _session(instrument_id, pool, repo, clock, connect_fn)

    start = time.perf_counter()
    with pytest.raises(_Stop):
        await session.run()
    elapsed = time.perf_counter() - start

    async with pool.acquire() as db_conn, db_conn.transaction():
        spans = await repo.list_spans(db_conn, instrument_id, Timeframe.L2)

    print(
        f"[RD-19 l2_ingest_session] 갭 {n_gaps}회 실 DB 사이클 {elapsed:.3f}s "
        f"(budget<{budget_sec}s)"
    )
    assert len(spans) == n_gaps  # 갭마다 1개씩 결손 span, 마지막 끊김이 추가로 닫지 않음(연결 소진)
    assert elapsed < budget_sec, (
        f"갭 {n_gaps}회 실 DB 사이클이 예산({budget_sec}s)을 넘었습니다({elapsed:.3f}s)."
    )
