"""RD-19 — `L2IngestSession` × 실 DB(`coverage_spans`) 통합테스트.

Spec: docs/design/ADR-2026-09-06-H-data-sourcing-self-build-and-contract-tiers.md
D3·D5, task-1766 DoD: "갭 주입 시 재동기화가 실제로 발생하고 손실 구간이
coverage_spans에 결손으로 기록됨(조용한 보간 금지)". 단위테스트
(`tests/unit/.../test_l2_ingest_session.py`)는 가짜 저장소로 이 동작을
검증했다 — 여기서는 실제 `PostgresCoverageRepository`·`coverage_spans`
EXCLUDE 제약까지 통과하는지, 그리고 `Venue.BINANCE`/`Timeframe.L2`가
DB CHECK 제약(마이그레이션 4b19195124bb)을 실제로 통과하는지 증명한다.
"""

from __future__ import annotations

import asyncio
import json
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


@pytest.fixture(autouse=True)
async def _cleanup_l2_coverage_rows(pool: asyncpg.Pool):
    """이 파일이 남기는 `Timeframe.L2` `coverage_spans` 행은 정리하지 않으면
    `test_downgrade_then_upgrade_round_trip`(migration round trip)이 4b19195124bb를
    downgrade할 때 되살리는 구 CHECK(`_OLD_TIMEFRAMES`에 'L2' 없음)를 위반해
    공유 TEST_DATABASE_URL 세션 전체를 깨뜨린다 — test_fa0c_account_scope.py의
    `_cleanup_portfolio_test_accounts`와 동일한 위생 규칙."""
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


async def test_gap_produces_two_non_overlapping_spans_with_a_real_hole_between(pool: asyncpg.Pool):
    """실 DB EXCLUDE 제약까지 통과하는지 포함해 갭→재동기화가 남기는
    손실 구간을 증명한다 — 두 span 사이는 어떤 span도 덮지 않는다."""
    instrument_id = _fake_ulid()
    await _insert_instrument(pool, instrument_id)
    repo = PostgresCoverageRepository(pool)
    adapter = _FakeBinanceAdapter()
    clock = _TickingClock(datetime(2026, 1, 1, tzinfo=timezone.utc))
    frames = [json.dumps({"seq": s}) for s in (1, 2, 4, 5)]  # 3 누락 → 갭
    conn = _FakeConnection(frames, raise_after=ConnectionClosed(None, None))
    connect_fn = _connect_sequence([conn])

    session = L2IngestSession(
        adapter=adapter,
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

    with pytest.raises(_Stop):
        await session.run()

    async with pool.acquire() as db_conn, db_conn.transaction():
        spans = await repo.list_spans(db_conn, instrument_id, Timeframe.L2)

    assert len(spans) == 2
    ordered = sorted(spans, key=lambda s: s.start)
    assert ordered[0].venue is Venue.BINANCE
    assert ordered[0].timeframe is Timeframe.L2
    # 손실 구간이 실제로 존재 — 두 span이 이어붙지 않는다(조용한 보간 금지).
    assert ordered[0].end < ordered[1].start


async def test_overlapping_span_insert_rejected_fail_closed(pool: asyncpg.Pool):
    """EXCLUDE 제약 위반 시 `CoverageSpanOverlapError`로 표면화한다 — 겹치는 손실
    구간이 조용히 병합/무시되지 않고 fail-closed로 거부되는지, L2IngestSession이
    의존하는 이 DB invariant를 직접 증명한다(§4.1)."""
    instrument_id = _fake_ulid()
    await _insert_instrument(pool, instrument_id)
    repo = PostgresCoverageRepository(pool)
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    span_a = CoverageSpan(
        instrument_id=instrument_id,
        venue=Venue.BINANCE,
        timeframe=Timeframe.L2,
        quality=CoverageQuality.PROVISIONAL,
        start=base,
        end=base + timedelta(minutes=10),
    )
    span_b = CoverageSpan(
        instrument_id=instrument_id,
        venue=Venue.BINANCE,
        timeframe=Timeframe.L2,
        quality=CoverageQuality.PROVISIONAL,
        start=base + timedelta(minutes=5),
        end=base + timedelta(minutes=15),
    )

    async with pool.acquire() as db_conn, db_conn.transaction():
        await repo.upsert_span(db_conn, span_a)

    with pytest.raises(CoverageSpanOverlapError):
        async with pool.acquire() as db_conn, db_conn.transaction():
            await repo.upsert_span(db_conn, span_b)


async def test_zero_duration_gap_records_no_phantom_span(pool: asyncpg.Pool):
    """clock이 멈춘 순간(재연결/재동기화가 시간 경과 없이 즉시 일어난 경우)에는
    `_close_span`의 `end_at <= start` 가드가 길이 0인 유령 span을 막아야 한다 —
    빈 구간을 span으로 기록하면 실제로는 관측하지 못한 시각을 커버리지로
    주장하게 되어 §4.1 금지사항을 어긴다."""
    instrument_id = _fake_ulid()
    await _insert_instrument(pool, instrument_id)
    repo = PostgresCoverageRepository(pool)
    adapter = _FakeBinanceAdapter()
    frozen = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def clock() -> datetime:
        return frozen

    frames = [json.dumps({"seq": s}) for s in (1, 3)]  # 즉시 갭 — 시계는 멈춘 채로
    conn = _FakeConnection(frames, raise_after=_Stop())
    connect_fn = _connect_sequence([conn])

    session = L2IngestSession(
        adapter=adapter,
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

    with pytest.raises(_Stop):
        await session.run()

    async with pool.acquire() as db_conn, db_conn.transaction():
        spans = await repo.list_spans(db_conn, instrument_id, Timeframe.L2)

    assert spans == []


async def test_coverage_write_failure_propagates_not_swallowed(
    pool: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch
):
    """`coverage_repo.upsert_span`이 실패하면 `L2IngestSession`은 예외를 삼키지
    않고 그대로 다시 던진다 — 손실 구간 기록 실패가 조용히 사라지지 않도록 하는
    fail-closed 경로(§4.1)를 의존성 예외 주입으로 증명한다."""
    instrument_id = _fake_ulid()
    await _insert_instrument(pool, instrument_id)
    repo = PostgresCoverageRepository(pool)

    async def _boom(conn: asyncpg.Connection, span: CoverageSpan) -> CoverageSpan:
        raise RuntimeError("주입된 커버리지 기록 실패")

    monkeypatch.setattr(repo, "upsert_span", _boom)

    adapter = _FakeBinanceAdapter()
    clock = _TickingClock(datetime(2026, 1, 1, tzinfo=timezone.utc))
    frames = [
        json.dumps({"seq": s}) for s in (1, 2, 4)
    ]  # 갭 → on_resync → _close_span → upsert_span
    conn = _FakeConnection(frames, raise_after=_Stop())
    connect_fn = _connect_sequence([conn])

    session = L2IngestSession(
        adapter=adapter,
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

    with pytest.raises(RuntimeError, match="주입된 커버리지 기록 실패"):
        await session.run()
