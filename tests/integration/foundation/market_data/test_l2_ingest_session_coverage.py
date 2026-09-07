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
    PostgresCoverageRepository,
)
from src.foundation.market_data.contracts.v1 import Timeframe, Venue
from src.foundation.market_data.domain.l2_orderbook import L2Diff, L2Snapshot


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
            sequence=int(message["seq"]), as_of=datetime.now(timezone.utc),
            bid_updates=(), ask_updates=(),
        )

    async def fetch_snapshot(self, instrument_symbol: str) -> L2Snapshot:
        return L2Snapshot(sequence=0, as_of=datetime.now(timezone.utc), bids={}, asks={})


def _fake_ulid() -> str:
    return "0" + uuid.uuid4().hex[:25].upper()


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
            "connect_fn": connect_fn, "sleep_fn": _no_sleep, "ping_sleep_fn": _never,
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
